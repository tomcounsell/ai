"""Telegram client management and initialization."""

import os
import time
from pyrogram import Client
from pyrogram.enums import ChatType
from pathlib import Path
from typing import Optional

from .handlers import MessageHandler
from .chat_history import ChatHistoryManager


class TelegramClient:
    """Manages Telegram client lifecycle and configuration."""
    
    def __init__(self, workdir: str = "/Users/valorengels/src/ai"):
        self.client: Optional[Client] = None
        self.workdir = workdir
        self.chat_history = ChatHistoryManager()
        self.message_handler: Optional[MessageHandler] = None
        self.bot_start_time = None
        
    async def initialize(self, notion_scout=None) -> bool:
        """Initialize the Telegram client with proper configuration."""
        try:
            self.bot_start_time = time.time()
            
            # Load existing chat history
            self.chat_history.load_history()
            
            api_id = os.getenv('TELEGRAM_API_ID')
            api_hash = os.getenv('TELEGRAM_API_HASH')
            
            print(f"Loading Telegram credentials: api_id={api_id}, api_hash={'*' * len(api_hash) if api_hash else None}")
            
            if not all([api_id, api_hash]):
                print("Telegram credentials not found in environment variables")
                return False
            
            self.client = Client(
                "ai_project_bot",
                api_id=int(api_id),
                api_hash=api_hash,
                workdir=self.workdir
            )
            
            # Start the client
            await self.client.start()
            print("Telegram client started successfully")
            
            # Initialize message handler
            self.message_handler = MessageHandler(
                client=self.client,
                chat_history=self.chat_history,
                notion_scout=notion_scout,
                bot_start_time=self.bot_start_time
            )
            
            # Register message handler
            @self.client.on_message()
            async def handle_message(client, message):
                await self.message_handler.handle_message(client, message)
            
            return True
            
        except Exception as e:
            print(f"Failed to start Telegram client: {e}")
            return False
    
    async def stop(self):
        """Stop the Telegram client and save state."""
        print("Saving chat history...")
        self.chat_history.save_history()
        
        if self.client:
            try:
                await self.client.stop()
                print("Telegram client stopped")
            except Exception as e:
                print(f"Error stopping Telegram client: {e}")
    
    @property
    def is_connected(self) -> bool:
        """Check if the client is connected."""
        return self.client and self.client.is_connected
    
    @property
    def session_name(self) -> str:
        """Get the client session name."""
        return getattr(self.client, 'session_name', 'ai_project_bot') if self.client else 'disconnected'