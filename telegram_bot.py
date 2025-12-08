#!/usr/bin/env python3
"""
Telegram Bot for AI Rebuild System
Connects to Telegram and responds to messages using the AI system
"""

import asyncio
import os
import sys
import logging
import traceback
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Any, Dict

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError
from dotenv import load_dotenv

from config import settings
from utilities.database import DatabaseManager
from agents.valor.context import ValorContext, MessageEntry
from agents.context_manager import ContextWindowManager
from agents.valor.agent import ValorAgent

# Load environment variables
load_dotenv()

# Setup verbose logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    handlers=[
        logging.FileHandler('logs/telegram_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# Also set telethon to debug level for connection issues
logging.getLogger('telethon').setLevel(logging.INFO)

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
        
        # Get phone and password for auto-login
        self.phone = os.getenv('TELEGRAM_PHONE')
        self.password = os.getenv('TELEGRAM_PASSWORD')
        
        # Load group configuration
        self.group_config = self._load_group_config()
        
        # Get chat filtering settings (backwards compatibility)
        self.allowed_groups = os.getenv('TELEGRAM_ALLOWED_GROUPS', '').strip('"').split(',') if os.getenv('TELEGRAM_ALLOWED_GROUPS') else []
        self.allow_dms = os.getenv('TELEGRAM_ALLOW_DMS', 'true').lower() == 'true'
        
        # Initialize AI components
        self.db_manager: Optional[DatabaseManager] = None
        self.context_manager: Optional[ContextWindowManager] = None
        self.valor_agent: Optional[ValorAgent] = None
        self.contexts = {}  # Store user contexts
        self.message_count = 0  # Track total messages
        self.error_count = 0  # Track errors

        # Check if we have API keys for real AI responses
        self.anthropic_api_key = os.getenv('ANTHROPIC_API_KEY')
        self.has_ai_keys = bool(self.anthropic_api_key and self.anthropic_api_key != 'your_anthropic_api_key_here')
        logger.debug(f"Initialized with groups: {self.allowed_groups}, DMs: {self.allow_dms}")
        logger.debug(f"Group config loaded: {len(self.group_config.get('groups', {}))} groups configured")
        
    def _load_group_config(self):
        """Load group configuration from JSON file"""
        config_path = Path(__file__).parent / 'config' / 'telegram_groups.json'
        try:
            if config_path.exists():
                with open(config_path, 'r') as f:
                    return json.load(f)
            else:
                logger.warning(f"Group config not found at {config_path}, using defaults")
                return {
                    "default_behavior": {
                        "respond_to_mentions": True,
                        "respond_to_all": False,
                        "respond_to_replies": True
                    },
                    "groups": {},
                    "direct_messages": {"enabled": True},
                    "mention_triggers": ["@valor", "valor"]
                }
        except Exception as e:
            logger.error(f"Error loading group config: {e}")
            return {}
    
    def _should_respond(self, event, chat_title=None):
        """Determine if we should respond to this message"""
        # Direct messages
        if event.is_private:
            dm_config = self.group_config.get('direct_messages', {})
            if not dm_config.get('enabled', True):
                return False
            
            # Check whitelist/blacklist
            sender_id = str(event.sender_id) if hasattr(event, 'sender_id') else None
            if sender_id:
                whitelist = dm_config.get('whitelist_users', [])
                blacklist = dm_config.get('blacklist_users', [])
                
                if whitelist and sender_id not in whitelist:
                    return False
                if blacklist and sender_id in blacklist:
                    return False
            
            return True
        
        # Group messages
        if event.is_group or event.is_channel:
            # Check if group is in config
            group_config = self.group_config.get('groups', {}).get(chat_title, 
                                                    self.group_config.get('default_behavior', {}))
            
            if not group_config.get('enabled', True):
                return False
            
            # Check if it's a reply to our message
            if event.is_reply and group_config.get('respond_to_replies', True):
                # TODO: Check if the replied message was from us
                return True
            
            # Check for mentions
            if group_config.get('respond_to_mentions', True):
                text_lower = event.text.lower() if event.text else ""
                mention_triggers = self.group_config.get('mention_triggers', ["@valor", "valor"])
                
                for trigger in mention_triggers:
                    if trigger.lower() in text_lower:
                        logger.debug(f"Mention detected: '{trigger}' in message")
                        return True
            
            # Check if we should respond to all messages
            if group_config.get('respond_to_all', False):
                return True
            
            # Check for keywords
            keywords = group_config.get('keywords', [])
            if keywords and event.text:
                text_lower = event.text.lower()
                for keyword in keywords:
                    if keyword.lower() in text_lower:
                        logger.debug(f"Keyword detected: '{keyword}' in message")
                        return True
        
        return False
    
    async def initialize_components(self):
        """Initialize AI system components"""
        logger.info("📦 Initializing AI components...")
        
        try:
            # Initialize database
            logger.debug("Creating DatabaseManager instance")
            self.db_manager = DatabaseManager()
            await self.db_manager.initialize()
            logger.info("✅ Database ready")
            logger.debug(f"Database path: {self.db_manager.db_path if hasattr(self.db_manager, 'db_path') else 'N/A'}")
            
            # Initialize context manager
            logger.debug("Creating ContextWindowManager with 100k tokens")
            self.context_manager = ContextWindowManager(max_tokens=100000)
            logger.info("✅ Context manager ready (100k tokens)")

            # Initialize Valor agent if API keys are available
            if self.has_ai_keys:
                logger.debug("Initializing ValorAgent with Anthropic Claude")
                self.valor_agent = ValorAgent(
                    model="anthropic:claude-sonnet-4-20250514",
                    max_context_tokens=100000,
                    debug=True
                )
                logger.info("✅ Valor agent ready (Claude Sonnet)")
            else:
                logger.warning("⚠️ No API keys found - running in demo mode")
        except Exception as e:
            logger.error(f"Failed to initialize components: {e}")
            logger.error(f"Stack trace:\n{traceback.format_exc()}")
            raise
        
    async def start(self):
        """Start the Telegram bot with verbose logging"""
        logger.info("🚀 Starting Telegram Bot...")
        logger.debug(f"Python version: {sys.version}")
        logger.debug(f"Telethon version: {TelegramClient.__version__ if hasattr(TelegramClient, '__version__') else 'unknown'}")
        
        # Initialize components
        await self.initialize_components()
        
        # Connect to Telegram with phone and password
        logger.debug(f"Connecting to Telegram with session: {self.session_name}")
        logger.debug(f"Phone configured: {'Yes' if self.phone else 'No'}")
        logger.debug(f"Password configured: {'Yes' if self.password else 'No'}")
        
        try:
            await self.client.start(
                phone=lambda: self.phone,
                password=lambda: self.password
            )
            logger.debug("Successfully authenticated with Telegram")
        except SessionPasswordNeededError:
            logger.error("2FA is enabled but no password provided in TELEGRAM_PASSWORD")
            raise
        except Exception as e:
            logger.error(f"Failed to connect to Telegram: {e}")
            logger.error(f"Connection error stack trace:\n{traceback.format_exc()}")
            raise
        
        # Get bot info
        me = await self.client.get_me()
        logger.info(f"✅ Bot connected as: {me.first_name} (@{me.username})")
        logger.info(f"📱 Phone: {me.phone}")
        logger.debug(f"User ID: {me.id}")
        logger.debug(f"Is bot: {me.bot if hasattr(me, 'bot') else 'N/A'}")
        logger.debug(f"Is verified: {me.verified if hasattr(me, 'verified') else 'N/A'}")
        
        # Register event handlers
        @self.client.on(events.NewMessage(incoming=True))
        async def handle_message(event):
            """Handle incoming messages with verbose logging"""
            self.message_count += 1
            message_metadata = {}
            
            try:
                # Get sender and chat info
                logger.debug(f"Processing message #{self.message_count}")
                sender = await event.get_sender()
                chat = await event.get_chat()
                
                # Collect comprehensive metadata
                message_metadata = {
                    'message_id': event.id,
                    'message_count': self.message_count,
                    'sender_id': sender.id if sender else None,
                    'sender_username': sender.username if sender else None,
                    'sender_first_name': sender.first_name if sender else None,
                    'sender_last_name': sender.last_name if sender else None,
                    'sender_phone': sender.phone if hasattr(sender, 'phone') else None,
                    'chat_id': event.chat_id,
                    'chat_type': 'private' if event.is_private else 'group' if event.is_group else 'channel',
                    'chat_title': getattr(chat, 'title', None),
                    'is_reply': event.is_reply,
                    'reply_to_msg_id': event.reply_to_msg_id if event.is_reply else None,
                    'text_length': len(event.text) if event.text else 0,
                    'has_media': bool(event.media),
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }
                
                logger.debug(f"Message metadata: {json.dumps(message_metadata, indent=2)}")
                
                # Check if we should handle this message
                chat_title = getattr(chat, 'title', None) if (event.is_group or event.is_channel) else None
                
                # Use new config-based logic
                should_respond = self._should_respond(event, chat_title)
                
                # Log decision
                if not should_respond:
                    if event.is_private:
                        logger.info(f"Ignoring DM from {sender.first_name} (@{sender.username}) based on config")
                    else:
                        logger.info(f"Ignoring message in '{chat_title}' - no trigger detected (mention/keyword)")
                    logger.debug(f"Message text: {event.text[:100] if event.text else 'None'}")
                    return
                
                # Special log for mentions
                if event.text and any(trigger in event.text.lower() for trigger in self.group_config.get('mention_triggers', [])):
                    logger.info(f"🔔 Mention detected in message from {sender.first_name} in {chat_title or 'DM'}")
                
                # Log the message with full context
                logger.info(f"📨 New message from {sender.first_name} (@{sender.username}) in {message_metadata['chat_type']}: {event.text[:100] if event.text else 'None'}...")
                logger.debug(f"Full message text: {event.text}")
                
                # Get or create context for this chat
                chat_id = str(event.chat_id)
                if chat_id not in self.contexts:
                    logger.debug(f"Creating new context for chat_id: {chat_id}")
                    self.contexts[chat_id] = ValorContext(
                        chat_id=chat_id,
                        user_name=sender.first_name or "User",
                        workspace="telegram"
                    )
                    logger.info(f"Created new context for {sender.first_name} (chat_id: {chat_id})")
                else:
                    logger.debug(f"Using existing context for chat_id: {chat_id}")
                
                context = self.contexts[chat_id]
                logger.debug(f"Context has {len(context.message_history)} messages in history")
                
                # Add message to context
                message_entry = MessageEntry(
                    role="user",
                    content=event.text,
                    timestamp=datetime.now(timezone.utc)
                )
                context.message_history.append(message_entry)
                logger.debug(f"Added message to context. History size: {len(context.message_history)}")
                
                # Get context stats
                stats = self.context_manager.get_context_stats(context)
                logger.debug(f"Context stats: {json.dumps(stats, indent=2)}")
                
                # Save to database
                if self.db_manager:
                    try:
                        logger.debug("Saving message to database")
                        db_metadata = {
                            "sender_id": str(sender.id),
                            "sender_name": sender.first_name,
                            "sender_username": sender.username,
                            "chat_type": message_metadata['chat_type'],
                            "message_id": event.id
                        }
                        message_id = await self.db_manager.add_chat_message(
                            project_id="telegram",
                            session_id=chat_id,
                            role="user",
                            content=event.text,
                            metadata=db_metadata
                        )
                        logger.debug(f"Message saved to database with ID: {message_id}")
                    except Exception as e:
                        logger.error(f"Database error: {e}")
                        logger.error(f"Database error stack trace:\n{traceback.format_exc()}")
                
                # Generate response using AI or demo mode
                logger.debug("Generating response")
                if self.valor_agent:
                    try:
                        agent_response = await self.valor_agent.process_message(
                            message=event.text,
                            chat_id=chat_id,
                            user_name=sender.first_name or sender.username or "User",
                            workspace="telegram"
                        )
                        response = agent_response.content
                        logger.debug(f"AI response generated, tools used: {agent_response.tools_used}")
                    except Exception as ai_error:
                        logger.error(f"AI generation failed: {ai_error}")
                        response = f"I encountered an error processing your message. Please try again."
                else:
                    response = self._generate_demo_response(event.text, stats)
                logger.debug(f"Generated response length: {len(response)} chars")
                
                # Send response
                logger.debug("Sending response via Telegram")
                sent_message = await event.reply(response)
                logger.info(f"✅ Response sent to {sender.first_name} (@{sender.username}) - Message ID: {sent_message.id}")
                logger.debug(f"Response content: {response[:200]}...")
                
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
                    logger.warning(f"⚠️ Context for {chat_id} approaching limit - {stats.get('total_tokens', 0)} tokens used")
                    logger.debug(f"Compression threshold reached for context: {chat_id}")
                    
            except Exception as e:
                self.error_count += 1
                logger.error(f"Error #{self.error_count} handling message: {e}")
                logger.error(f"Error type: {type(e).__name__}")
                logger.error(f"Error details: {str(e)}")
                logger.error(f"Stack trace:\n{traceback.format_exc()}")
                logger.error(f"Message metadata at error: {json.dumps(message_metadata, indent=2)}")
                
                try:
                    await event.reply(f"❌ Error: {str(e)}")
                    logger.debug("Error message sent to user")
                except Exception as reply_error:
                    logger.error(f"Failed to send error message to user: {reply_error}")
                    logger.error(f"Reply error stack trace:\n{traceback.format_exc()}")
        
        logger.info("🎯 Bot is running! Send messages to test...")
        logger.info("Press Ctrl+C to stop")
        logger.debug(f"Bot stats: Messages: {self.message_count}, Errors: {self.error_count}")
        
        # Monitor connection status
        @self.client.on(events.Raw())
        async def log_raw_events(event):
            """Log all raw events for debugging"""
            if hasattr(event, '__class__'):
                event_type = event.__class__.__name__
                if 'Update' in event_type or 'Connect' in event_type:
                    logger.debug(f"Raw event: {event_type}")
        
        # Keep the bot running
        logger.debug("Starting run_until_disconnected loop")
        await self.client.run_until_disconnected()
        
    def _generate_demo_response(self, message: str, stats: dict) -> str:
        """Generate a demo response without API keys"""
        # Get stats safely - handle both old and new format
        token_total = stats.get('total_tokens', 0)  # Changed from token_usage.total
        token_max = stats.get('max_tokens', 100000)  # Changed from token_usage.max_tokens
        message_count = stats.get('message_count', 0)  # Changed from message_count.total
        
        return f"""🤖 AI Rebuild Bot (Demo Mode)

Received: "{message[:100]}"

📊 Context Stats:
• Tokens: {token_total}/{token_max}
• Messages: {message_count}
• Quality: 9.8/10

💡 Note: Running in demo mode. Add API keys to enable full AI responses.

Available commands:
/status - System status
/help - Show help
/stats - Context statistics"""
    
    async def cleanup(self):
        """Clean up resources with logging"""
        logger.info("Cleaning up resources...")
        logger.debug(f"Final stats - Messages: {self.message_count}, Errors: {self.error_count}")
        
        if self.db_manager:
            logger.debug("Closing database connection")
            await self.db_manager.close()
            logger.debug("Database closed")
        
        logger.debug("Disconnecting from Telegram")
        await self.client.disconnect()
        logger.info("Bot disconnected successfully")

async def main():
    """Main entry point with comprehensive error handling"""
    print("\n" + "="*60)
    print("🤖 AI REBUILD TELEGRAM BOT")
    print("="*60 + "\n")
    
    logger.debug("Main function started")
    logger.debug(f"Working directory: {os.getcwd()}")
    logger.debug(f"Environment variables loaded: {len(os.environ)} variables")
    
    bot = TelegramBot()
    
    try:
        await bot.start()
    except KeyboardInterrupt:
        logger.info("\n⏹️ Received keyboard interrupt, stopping bot...")
        await bot.cleanup()
        logger.info("✅ Bot stopped gracefully")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        logger.error(f"Fatal error type: {type(e).__name__}")
        logger.error(f"Fatal error stack trace:\n{traceback.format_exc()}")
        await bot.cleanup()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())