#!/usr/bin/env python3
"""
Test script to fetch and analyze available Telegram reactions.

This script uses the raw Telegram API to:
1. Fetch all available reactions
2. Check reactions for specific chats/groups
3. Compare with the current hardcoded list
4. Identify premium vs regular reactions
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

# Add parent directory to Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

from pyrogram import Client
from pyrogram.raw import functions, types
from pyrogram.errors import FloodWait

from integrations.telegram.reaction_manager import reaction_manager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ReactionTester:
    """Test and analyze Telegram reactions."""
    
    def __init__(self):
        """Initialize the reaction tester."""
        self.client = None
        self.available_reactions = []
        self.premium_reactions = []
        self.regular_reactions = []
        self.custom_emoji_reactions = []
        
    async def initialize_client(self):
        """Initialize Telegram client."""
        api_id = os.getenv("TELEGRAM_API_ID")
        api_hash = os.getenv("TELEGRAM_API_HASH")
        
        if not api_id or not api_hash:
            raise ValueError("TELEGRAM_API_ID and TELEGRAM_API_HASH must be set")
        
        # Use a separate session for testing
        self.client = Client(
            "reaction_test_session",  # Separate session name
            api_id=int(api_id),
            api_hash=api_hash,
            workdir="/Users/valorengels/src/ai"
        )
        
        await self.client.start()
        logger.info("Telegram client initialized")
        
    async def fetch_available_reactions(self):
        """Fetch all available reactions using raw API."""
        try:
            # Get available reactions
            result = await self.client.invoke(
                functions.messages.GetAvailableReactions(hash=0)
            )
            
            if hasattr(result, 'reactions'):
                logger.info(f"Fetched {len(result.reactions)} available reactions")
                
                for reaction in result.reactions:
                    reaction_data = {
                        'emoticon': reaction.reaction,
                        'title': getattr(reaction, 'title', 'Unknown'),
                        'static_icon': hasattr(reaction, 'static_icon'),
                        'appear_animation': hasattr(reaction, 'appear_animation'),
                        'select_animation': hasattr(reaction, 'select_animation'),
                        'activate_animation': hasattr(reaction, 'activate_animation'),
                        'effect_animation': hasattr(reaction, 'effect_animation'),
                        'is_premium': getattr(reaction, 'premium', False),
                        'inactive': getattr(reaction, 'inactive', False),
                        'around_animation': hasattr(reaction, 'around_animation'),
                        'center_icon': hasattr(reaction, 'center_icon')
                    }
                    
                    self.available_reactions.append(reaction_data)
                    
                    # Categorize reactions
                    if reaction_data['is_premium']:
                        self.premium_reactions.append(reaction_data['emoticon'])
                    else:
                        self.regular_reactions.append(reaction_data['emoticon'])
                        
                return result
            else:
                logger.warning("No reactions found in response")
                return None
                
        except FloodWait as e:
            logger.warning(f"FloodWait: sleeping for {e.value} seconds")
            await asyncio.sleep(e.value)
            return await self.fetch_available_reactions()
        except Exception as e:
            logger.error(f"Error fetching reactions: {e}")
            return None
            
    async def fetch_top_reactions(self):
        """Fetch top/featured reactions."""
        try:
            result = await self.client.invoke(
                functions.messages.GetTopReactions(limit=100, hash=0)
            )
            
            if hasattr(result, 'reactions'):
                logger.info(f"Fetched {len(result.reactions)} top reactions")
                return [r.emoticon if hasattr(r, 'emoticon') else str(r) for r in result.reactions]
            return []
            
        except Exception as e:
            logger.error(f"Error fetching top reactions: {e}")
            return []
            
    async def fetch_recent_reactions(self):
        """Fetch recently used reactions."""
        try:
            result = await self.client.invoke(
                functions.messages.GetRecentReactions(limit=100, hash=0)
            )
            
            if hasattr(result, 'reactions'):
                logger.info(f"Fetched {len(result.reactions)} recent reactions")
                return [r.emoticon if hasattr(r, 'emoticon') else str(r) for r in result.reactions]
            return []
            
        except Exception as e:
            logger.error(f"Error fetching recent reactions: {e}")
            return []
            
    async def test_chat_reactions(self, chat_id: int = None):
        """Test reactions available in a specific chat."""
        if not chat_id:
            # Try to get a chat from dialogs
            async for dialog in self.client.get_dialogs(limit=1):
                chat_id = dialog.chat.id
                logger.info(f"Testing with chat: {dialog.chat.title or dialog.chat.first_name}")
                break
                
        if not chat_id:
            logger.warning("No chat found for testing")
            return
            
        try:
            # Get full chat info
            peer = await self.client.resolve_peer(chat_id)
            
            if hasattr(peer, 'channel_id'):
                # It's a channel/group
                result = await self.client.invoke(
                    functions.channels.GetFullChannel(channel=peer)
                )
                chat_full = result.full_chat
            else:
                # It's a regular chat
                result = await self.client.invoke(
                    functions.messages.GetFullChat(chat_id=abs(chat_id))
                )
                chat_full = result.full_chat
                
            # Check available reactions in this chat
            if hasattr(chat_full, 'available_reactions'):
                logger.info(f"Chat has custom reaction settings")
                if hasattr(chat_full.available_reactions, 'reactions'):
                    allowed = [r.emoticon if hasattr(r, 'emoticon') else str(r) 
                              for r in chat_full.available_reactions.reactions]
                    logger.info(f"Allowed reactions in chat: {allowed}")
                    return allowed
                    
        except Exception as e:
            logger.error(f"Error checking chat reactions: {e}")
            
        return None
        
    def compare_with_hardcoded(self):
        """Compare fetched reactions with hardcoded list."""
        hardcoded = reaction_manager.valid_telegram_emojis
        fetched = set(r['emoticon'] for r in self.available_reactions)
        
        logger.info(f"\n{'='*60}")
        logger.info("REACTION COMPARISON")
        logger.info(f"{'='*60}")
        
        logger.info(f"Hardcoded reactions: {len(hardcoded)}")
        logger.info(f"Fetched reactions: {len(fetched)}")
        logger.info(f"Regular reactions: {len(self.regular_reactions)}")
        logger.info(f"Premium reactions: {len(self.premium_reactions)}")
        
        # Missing from hardcoded
        missing_from_hardcoded = fetched - hardcoded
        if missing_from_hardcoded:
            logger.info(f"\nMissing from hardcoded list ({len(missing_from_hardcoded)}):")
            for emoji in sorted(missing_from_hardcoded):
                reaction_info = next((r for r in self.available_reactions if r['emoticon'] == emoji), {})
                premium_tag = " [PREMIUM]" if reaction_info.get('is_premium') else ""
                logger.info(f"  {emoji} - {reaction_info.get('title', 'Unknown')}{premium_tag}")
                
        # Not available in API
        not_in_api = hardcoded - fetched
        if not_in_api:
            logger.info(f"\nIn hardcoded but not in API ({len(not_in_api)}):")
            for emoji in sorted(not_in_api):
                logger.info(f"  {emoji}")
                
        # Properly available
        properly_available = hardcoded & fetched
        logger.info(f"\nProperly available reactions: {len(properly_available)}")
        
    def generate_dynamic_list(self):
        """Generate Python code for dynamic reaction list."""
        logger.info(f"\n{'='*60}")
        logger.info("GENERATED REACTION LISTS")
        logger.info(f"{'='*60}")
        
        # Regular reactions
        logger.info("\n# Regular reactions (available to all users):")
        logger.info("REGULAR_REACTIONS = {")
        for emoji in sorted(self.regular_reactions):
            reaction_info = next((r for r in self.available_reactions if r['emoticon'] == emoji), {})
            logger.info(f'    "{emoji}",  # {reaction_info.get("title", "Unknown")}')
        logger.info("}")
        
        # Premium reactions
        if self.premium_reactions:
            logger.info("\n# Premium reactions (Telegram Premium only):")
            logger.info("PREMIUM_REACTIONS = {")
            for emoji in sorted(self.premium_reactions):
                reaction_info = next((r for r in self.available_reactions if r['emoticon'] == emoji), {})
                logger.info(f'    "{emoji}",  # {reaction_info.get("title", "Unknown")}')
            logger.info("}")
            
    def save_results(self):
        """Save results to JSON file."""
        output = {
            'timestamp': asyncio.get_event_loop().time(),
            'total_reactions': len(self.available_reactions),
            'regular_reactions': sorted(self.regular_reactions),
            'premium_reactions': sorted(self.premium_reactions),
            'all_reactions': self.available_reactions,
            'comparison': {
                'hardcoded_count': len(reaction_manager.valid_telegram_emojis),
                'fetched_count': len(self.available_reactions),
                'missing_from_hardcoded': sorted(list(
                    set(r['emoticon'] for r in self.available_reactions) - 
                    reaction_manager.valid_telegram_emojis
                )),
                'not_in_api': sorted(list(
                    reaction_manager.valid_telegram_emojis - 
                    set(r['emoticon'] for r in self.available_reactions)
                ))
            }
        }
        
        output_file = Path(__file__).parent / 'available_reactions.json'
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
            
        logger.info(f"\nResults saved to: {output_file}")
        
    async def run_tests(self):
        """Run all reaction tests."""
        try:
            await self.initialize_client()
            
            # Fetch all available reactions
            await self.fetch_available_reactions()
            
            # Fetch top reactions
            top_reactions = await self.fetch_top_reactions()
            if top_reactions:
                logger.info(f"\nTop reactions: {' '.join(top_reactions[:10])}...")
                
            # Fetch recent reactions
            recent_reactions = await self.fetch_recent_reactions()
            if recent_reactions:
                logger.info(f"Recent reactions: {' '.join(recent_reactions[:10])}...")
                
            # Test chat-specific reactions
            await self.test_chat_reactions()
            
            # Compare with hardcoded list
            self.compare_with_hardcoded()
            
            # Generate dynamic lists
            self.generate_dynamic_list()
            
            # Save results
            self.save_results()
            
        finally:
            if self.client:
                await self.client.stop()
                

async def create_reaction_utility():
    """Create utility function for dynamic reaction fetching."""
    
    utility_code = '''"""
Dynamic reaction fetching utility for Telegram.

This module provides functions to dynamically fetch available reactions
instead of relying on hardcoded lists.
"""

import asyncio
import logging
from typing import Set, Dict, List, Optional
from datetime import datetime, timedelta

from pyrogram import Client
from pyrogram.raw import functions, types
from pyrogram.errors import FloodWait

logger = logging.getLogger(__name__)


class DynamicReactionManager:
    """Manages dynamic fetching and caching of Telegram reactions."""
    
    def __init__(self, cache_duration: int = 3600):
        """
        Initialize the dynamic reaction manager.
        
        Args:
            cache_duration: How long to cache reactions in seconds (default: 1 hour)
        """
        self.cache_duration = cache_duration
        self._cache = {
            'reactions': set(),
            'regular': set(),
            'premium': set(),
            'last_updated': None,
            'hash': 0
        }
        
    async def get_available_reactions(self, client: Client, force_refresh: bool = False) -> Set[str]:
        """
        Get all available reactions, using cache if valid.
        
        Args:
            client: Pyrogram client instance
            force_refresh: Force refresh even if cache is valid
            
        Returns:
            Set of available reaction emojis
        """
        if not force_refresh and self._is_cache_valid():
            return self._cache['reactions'].copy()
            
        try:
            result = await client.invoke(
                functions.messages.GetAvailableReactions(hash=self._cache['hash'])
            )
            
            if hasattr(result, 'reactions'):
                reactions = set()
                regular = set()
                premium = set()
                
                for reaction in result.reactions:
                    emoji = reaction.reaction
                    reactions.add(emoji)
                    
                    if getattr(reaction, 'premium', False):
                        premium.add(emoji)
                    else:
                        regular.add(emoji)
                        
                # Update cache
                self._cache.update({
                    'reactions': reactions,
                    'regular': regular,
                    'premium': premium,
                    'last_updated': datetime.now(),
                    'hash': getattr(result, 'hash', 0)
                })
                
                logger.info(f"Updated reaction cache: {len(reactions)} total "
                          f"({len(regular)} regular, {len(premium)} premium)")
                
                return reactions.copy()
                
        except FloodWait as e:
            logger.warning(f"FloodWait: waiting {e.value} seconds")
            await asyncio.sleep(e.value)
            return await self.get_available_reactions(client, force_refresh)
        except Exception as e:
            logger.error(f"Error fetching reactions: {e}")
            
        # Return cached data if available, even if expired
        return self._cache['reactions'].copy() if self._cache['reactions'] else set()
        
    async def get_regular_reactions(self, client: Client) -> Set[str]:
        """Get only regular (non-premium) reactions."""
        await self.get_available_reactions(client)
        return self._cache['regular'].copy()
        
    async def get_premium_reactions(self, client: Client) -> Set[str]:
        """Get only premium reactions."""
        await self.get_available_reactions(client)
        return self._cache['premium'].copy()
        
    async def is_valid_reaction(self, client: Client, emoji: str) -> bool:
        """
        Check if an emoji is a valid Telegram reaction.
        
        Args:
            client: Pyrogram client instance
            emoji: Emoji to check
            
        Returns:
            True if emoji is a valid reaction
        """
        reactions = await self.get_available_reactions(client)
        return emoji in reactions
        
    async def get_chat_reactions(self, client: Client, chat_id: int) -> Optional[Set[str]]:
        """
        Get reactions available in a specific chat.
        
        Args:
            client: Pyrogram client instance
            chat_id: Chat ID to check
            
        Returns:
            Set of allowed reactions, or None if all reactions are allowed
        """
        try:
            peer = await client.resolve_peer(chat_id)
            
            if hasattr(peer, 'channel_id'):
                result = await client.invoke(
                    functions.channels.GetFullChannel(channel=peer)
                )
                chat_full = result.full_chat
            else:
                result = await client.invoke(
                    functions.messages.GetFullChat(chat_id=abs(chat_id))
                )
                chat_full = result.full_chat
                
            if hasattr(chat_full, 'available_reactions'):
                if hasattr(chat_full.available_reactions, 'reactions'):
                    return set(
                        r.emoticon if hasattr(r, 'emoticon') else str(r)
                        for r in chat_full.available_reactions.reactions
                    )
                    
        except Exception as e:
            logger.error(f"Error getting chat reactions: {e}")
            
        return None
        
    def _is_cache_valid(self) -> bool:
        """Check if the cache is still valid."""
        if not self._cache['last_updated']:
            return False
            
        age = datetime.now() - self._cache['last_updated']
        return age.total_seconds() < self.cache_duration


# Global instance
dynamic_reactions = DynamicReactionManager()


# Convenience functions
async def get_valid_reactions(client: Client) -> Set[str]:
    """Get all valid Telegram reactions."""
    return await dynamic_reactions.get_available_reactions(client)


async def is_valid_reaction(client: Client, emoji: str) -> bool:
    """Check if an emoji is a valid Telegram reaction."""
    return await dynamic_reactions.is_valid_reaction(client, emoji)


async def get_chat_allowed_reactions(client: Client, chat_id: int) -> Optional[Set[str]]:
    """Get reactions allowed in a specific chat."""
    return await dynamic_reactions.get_chat_reactions(client, chat_id)
'''
    
    # Save the utility module
    utility_file = Path(__file__).parent.parent / 'integrations' / 'telegram' / 'dynamic_reactions.py'
    with open(utility_file, 'w') as f:
        f.write(utility_code)
        
    logger.info(f"Created dynamic reaction utility at: {utility_file}")


async def main():
    """Main test function."""
    # Load environment variables
    from dotenv import load_dotenv
    load_dotenv()
    
    # Run tests
    tester = ReactionTester()
    await tester.run_tests()
    
    # Create utility module
    await create_reaction_utility()
    
    logger.info("\nAll tests completed!")


if __name__ == "__main__":
    asyncio.run(main())