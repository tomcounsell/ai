"""Telegram client management and initialization."""

import os
import time
from datetime import datetime

from pyrogram import Client

from .chat_history import ChatHistoryManager
from .handlers import MessageHandler


class TelegramClient:
    """Manages Telegram client lifecycle and configuration."""

    def __init__(self, workdir: str = "/Users/valorengels/src/ai"):
        self.client: Client | None = None
        self.workdir = workdir
        self.chat_history = ChatHistoryManager()
        self.message_handler: MessageHandler | None = None
        self.bot_start_time = None

    async def initialize(self, notion_scout=None) -> bool:
        """Initialize the Telegram client with proper configuration."""
        try:
            self.bot_start_time = time.time()

            # Load existing chat history
            self.chat_history.load_history()

            api_id = os.getenv("TELEGRAM_API_ID")
            api_hash = os.getenv("TELEGRAM_API_HASH")

            print(
                f"Loading Telegram credentials: api_id={api_id}, api_hash={'*' * len(api_hash) if api_hash else None}"
            )

            if not all([api_id, api_hash]):
                print("Telegram credentials not found in environment variables")
                return False

            # Create client with better session handling to prevent database locks
            self.client = Client(
                "ai_project_bot", 
                api_id=int(api_id), 
                api_hash=api_hash, 
                workdir=self.workdir,
                max_concurrent_transmissions=1  # Reduce concurrent transmissions to prevent locks
            )

            # Start the client
            await self.client.start()
            print("Telegram client started successfully")

            # Initialize message handler
            self.message_handler = MessageHandler(
                client=self.client,
                chat_history=self.chat_history,
                notion_scout=notion_scout,
                bot_start_time=self.bot_start_time,
            )

            # Check for missed messages during startup
            await self._check_startup_missed_messages(notion_scout)

            # Register message handler
            @self.client.on_message()
            async def handle_message(client, message):
                print(f"DEBUG: Received message from {message.from_user.username if message.from_user else 'unknown'}: {message.text[:50] if message.text else 'non-text'}")
                await self.message_handler.handle_message(client, message)

            # Test message handling with self-ping
            await self._test_message_handling()

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

    async def list_active_dialogs(self) -> dict:
        """List all active Telegram groups and DMs with their details.
        
        Returns:
            dict: Dictionary with 'groups' and 'dms' keys, each containing
                  list of chat details (id, title, type, member_count if applicable)
        
        Raises:
            ConnectionError: If client is not connected
            PermissionError: If lacking API permissions
            Exception: For other API errors (rate limits, etc.)
        """
        if not self.client or not self.client.is_connected:
            raise ConnectionError("Telegram client is not connected")

        try:
            from pyrogram.enums import ChatType
            
            groups = []
            dms = []
            
            # Get all dialogs (conversations)
            async for dialog in self.client.get_dialogs():
                chat = dialog.chat
                
                chat_info = {
                    'id': chat.id,
                    'title': getattr(chat, 'title', None) or getattr(chat, 'first_name', 'Unknown'),
                    'type': chat.type.name,
                    'username': getattr(chat, 'username', None),
                    'is_verified': getattr(chat, 'is_verified', False),
                    'is_restricted': getattr(chat, 'is_restricted', False),
                    'unread_count': dialog.unread_messages_count,
                    'last_message_date': dialog.top_message.date if dialog.top_message else None
                }
                
                # Categorize based on chat type
                if chat.type in [ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL]:
                    # Add group-specific information
                    chat_info['member_count'] = getattr(chat, 'members_count', None)
                    chat_info['description'] = getattr(chat, 'description', None)
                    groups.append(chat_info)
                elif chat.type == ChatType.PRIVATE:
                    # Add DM-specific information
                    chat_info['last_name'] = getattr(chat, 'last_name', None)
                    chat_info['phone_number'] = getattr(chat, 'phone_number', None)
                    chat_info['is_contact'] = getattr(chat, 'is_contact', False)
                    dms.append(chat_info)
                    
            return {
                'groups': groups,
                'dms': dms,
                'total_groups': len(groups),
                'total_dms': len(dms),
                'total_dialogs': len(groups) + len(dms)
            }
            
        except Exception as e:
            # Handle specific API errors
            if "FLOOD_WAIT" in str(e):
                raise Exception(f"Rate limit exceeded: {e}")
            elif "AUTH_KEY" in str(e) or "SESSION" in str(e):
                raise PermissionError(f"Authentication error: {e}")
            elif "ACCESS_DENIED" in str(e):
                raise PermissionError(f"Access denied: {e}")
            else:
                raise Exception(f"Failed to retrieve dialogs: {e}")

    @property
    def is_connected(self) -> bool:
        """Check if the client is connected."""
        return self.client and self.client.is_connected

    @property
    def session_name(self) -> str:
        """Get the client session name."""
        return (
            getattr(self.client, "session_name", "ai_project_bot")
            if self.client
            else "disconnected"
        )

    async def _check_startup_missed_messages(self, notion_scout=None):
        """Check for missed messages during startup and process them."""
        if not self.client or not self.message_handler:
            return

        print("🔍 Checking for missed messages during startup...")
        print(f"   Bot started at: {self.bot_start_time} ({datetime.fromtimestamp(self.bot_start_time).strftime('%Y-%m-%d %H:%M:%S')})")
        print(f"   Catchup window: Last 5 minutes before startup")
        
        try:
            from .utils import is_message_too_old
            from pyrogram.enums import ChatType

            missed_message_count = 0
            processed_chats = []

            # Get all dialogs (conversations) - this includes both DMs and groups
            async for dialog in self.client.get_dialogs():
                chat = dialog.chat
                chat_id = chat.id
                
                # Check if this chat should be handled by this server instance
                is_private_chat = chat.type == ChatType.PRIVATE
                if not self.message_handler._should_handle_chat(chat_id, is_private_chat):
                    continue

                processed_chats.append(chat_id)
                chat_missed_messages = []

                # Get recent messages from this chat (last 50 messages, but we'll filter by time)
                try:
                    message_count = 0
                    async for message in self.client.get_chat_history(chat_id, limit=50):
                        message_count += 1
                        
                        # Skip non-text messages for startup check
                        if not message.text:
                            continue
                            
                        # Check if this is a missed message (sent while bot was offline)
                        message_time = message.date.timestamp()
                        
                        # Define catchup window (5 minutes before bot start)
                        catchup_window_start = self.bot_start_time - 300  # 5 minutes
                        
                        # Message is missed if it's:
                        # 1. From before bot started
                        # 2. But within the catchup window
                        if catchup_window_start < message_time < self.bot_start_time:
                            # This is a missed message - collect it
                            chat_missed_messages.append(message.text)
                            missed_message_count += 1
                            
                            # Store the message in chat history for context
                            self.chat_history.add_message(chat_id, "user", message.text)
                            
                            print(f"Found missed message in chat {chat_id}: {message.text[:50]}...")
                            print(f"  Message time: {message_time}, Bot start: {self.bot_start_time}")
                        
                        # Skip messages that are too old (before catchup window)
                        elif message_time < catchup_window_start:
                            # We've gone too far back, no need to check older messages
                            break

                except Exception as e:
                    print(f"Warning: Could not check messages in chat {chat_id}: {e}")
                    continue

                # If we found missed messages for this chat, add them to the handler
                if chat_missed_messages:
                    if chat_id not in self.message_handler.missed_messages_per_chat:
                        self.message_handler.missed_messages_per_chat[chat_id] = []
                    
                    # Add messages in chronological order (reverse the list since we got newest first)
                    self.message_handler.missed_messages_per_chat[chat_id].extend(reversed(chat_missed_messages))

            if missed_message_count > 0:
                print(f"✅ Found {missed_message_count} missed messages across {len(processed_chats)} chats")
                print(f"📬 Missed messages will be processed when users send new messages")
            else:
                print("✅ No missed messages found during startup")

        except Exception as e:
            print(f"❌ Error checking for missed messages during startup: {e}")
            # Don't fail startup if missed message check fails

    async def _test_message_handling(self):
        """Test message handling by sending a self-ping to verify the system works end-to-end."""
        if not self.client or not self.message_handler:
            return
        
        print("🔄 Testing message handling with self-ping...")
        
        try:
            # Get own user info
            me = await self.client.get_me()
            my_user_id = me.id
            
            # Send a test message to ourselves
            test_message = "🔄 System test: ping"
            await self.client.send_message("me", test_message)
            
            # Wait a moment for the message to be processed
            import asyncio
            await asyncio.sleep(2)
            
            # Check if the message was processed by looking at chat history
            if my_user_id in self.chat_history.chat_histories:
                recent_messages = self.chat_history.chat_histories[my_user_id]
                
                # Look for our test message in recent history
                test_found = any(test_message in msg.get("content", "") for msg in recent_messages[-3:])
                
                if test_found:
                    print("✅ Self-ping test successful - message handling is operational")
                else:
                    print("⚠️  Self-ping test: message sent but not processed (check whitelist)")
            else:
                print("⚠️  Self-ping test: no chat history found (check whitelist configuration)")
                
        except Exception as e:
            print(f"⚠️  Self-ping test failed: {e}")
            print("   This indicates message handling may not be working properly")
            # Don't fail startup, but warn the user
