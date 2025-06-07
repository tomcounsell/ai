"""
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
