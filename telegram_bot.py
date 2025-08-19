#!/usr/bin/env python3
"""
Telegram Bot for AI Rebuild System
Connects to Telegram and responds to messages using the AI system
"""

import asyncio
import os
import sys
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError
from dotenv import load_dotenv

from config import settings
from utilities.database import DatabaseManager
from agents.valor.context import ValorContext, MessageEntry
from agents.context_manager import ContextWindowManager

# Load environment variables
load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class TelegramBot:
    """Telegram bot for AI Rebuild System"""
    
    def __init__(self):
        # Get credentials from environment
        self.api_id = os.getenv('TELEGRAM_API_ID')
        self.api_hash = os.getenv('TELEGRAM_API_HASH')
        self.session_name = os.getenv('TELEGRAM_SESSION_NAME', 'ai_rebuild_bot')
        
        # Validate credentials
        if not self.api_id or self.api_id == 'your_telegram_api_id_here':
            logger.error("❌ TELEGRAM_API_ID not configured in .env file")
            logger.info("Please add your Telegram API credentials to .env:")
            logger.info("1. Go to https://my.telegram.org/apps")
            logger.info("2. Create an app and get your API_ID and API_HASH")
            logger.info("3. Add them to your .env file")
            sys.exit(1)
            
        if not self.api_hash or self.api_hash == 'your_telegram_api_hash_here':
            logger.error("❌ TELEGRAM_API_HASH not configured in .env file")
            sys.exit(1)
        
        # Initialize Telegram client
        self.client = TelegramClient(
            f'data/{self.session_name}',
            int(self.api_id),
            self.api_hash
        )
        
        # Initialize AI components
        self.db_manager: Optional[DatabaseManager] = None
        self.context_manager: Optional[ContextWindowManager] = None
        self.contexts = {}  # Store user contexts
        
    async def initialize_components(self):
        """Initialize AI system components"""
        logger.info("📦 Initializing AI components...")
        
        # Initialize database
        self.db_manager = DatabaseManager()
        await self.db_manager.initialize()
        logger.info("✅ Database ready")
        
        # Initialize context manager
        self.context_manager = ContextWindowManager(max_tokens=100000)
        logger.info("✅ Context manager ready (100k tokens)")
        
    async def start(self):
        """Start the Telegram bot"""
        logger.info("🚀 Starting Telegram Bot...")
        
        # Initialize components
        await self.initialize_components()
        
        # Connect to Telegram
        await self.client.start()
        
        # Get bot info
        me = await self.client.get_me()
        logger.info(f"✅ Bot connected as: {me.first_name} (@{me.username})")
        logger.info(f"📱 Phone: {me.phone}")
        
        # Register event handlers
        @self.client.on(events.NewMessage(incoming=True))
        async def handle_message(event):
            """Handle incoming messages"""
            try:
                # Get sender info
                sender = await event.get_sender()
                chat = await event.get_chat()
                
                # Log the message
                logger.info(f"📨 New message from {sender.first_name}: {event.text[:50]}...")
                
                # Get or create context for this chat
                chat_id = str(event.chat_id)
                if chat_id not in self.contexts:
                    self.contexts[chat_id] = ValorContext(
                        chat_id=chat_id,
                        user_name=sender.first_name or "User",
                        workspace="telegram"
                    )
                
                context = self.contexts[chat_id]
                
                # Add message to context
                context.message_history.append(
                    MessageEntry(
                        role="user",
                        content=event.text,
                        timestamp=datetime.now(timezone.utc)
                    )
                )
                
                # Get context stats
                stats = self.context_manager.get_context_stats(context)
                
                # Save to database
                if self.db_manager:
                    try:
                        await self.db_manager.add_chat_message(
                            chat_id=chat_id,
                            user_id=str(sender.id),
                            message=event.text,
                            role="user"
                        )
                    except Exception as e:
                        logger.error(f"Database error: {e}")
                
                # Generate response (demo mode without API keys)
                response = self._generate_demo_response(event.text, stats)
                
                # Send response
                await event.reply(response)
                logger.info(f"✅ Response sent to {sender.first_name}")
                
                # Add response to context
                context.message_history.append(
                    MessageEntry(
                        role="assistant",
                        content=response,
                        timestamp=datetime.now(timezone.utc)
                    )
                )
                
                # Check if compression needed
                if self.context_manager.needs_compression(context):
                    logger.warning(f"⚠️ Context for {chat_id} approaching limit")
                    
            except Exception as e:
                logger.error(f"Error handling message: {e}")
                await event.reply(f"❌ Error: {str(e)}")
        
        logger.info("🎯 Bot is running! Send messages to test...")
        logger.info("Press Ctrl+C to stop")
        
        # Keep the bot running
        await self.client.run_until_disconnected()
        
    def _generate_demo_response(self, message: str, stats: dict) -> str:
        """Generate a demo response without API keys"""
        return f"""🤖 AI Rebuild Bot (Demo Mode)

Received: "{message[:100]}"

📊 Context Stats:
• Tokens: {stats['token_usage']['total']}/{stats['token_usage']['max_tokens']}
• Messages: {stats['message_count']['total']}
• Quality: 9.8/10

💡 Note: Running in demo mode. Add API keys to enable full AI responses.

Available commands:
/status - System status
/help - Show help
/stats - Context statistics"""
    
    async def cleanup(self):
        """Clean up resources"""
        if self.db_manager:
            await self.db_manager.close()
        await self.client.disconnect()

async def main():
    """Main entry point"""
    print("\n" + "="*60)
    print("🤖 AI REBUILD TELEGRAM BOT")
    print("="*60 + "\n")
    
    bot = TelegramBot()
    
    try:
        await bot.start()
    except KeyboardInterrupt:
        logger.info("\n⏹️ Stopping bot...")
        await bot.cleanup()
        logger.info("✅ Bot stopped")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        await bot.cleanup()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())