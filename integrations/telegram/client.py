"""Telegram client management and initialization."""

import logging
import os
import time

from pyrogram import Client

from .chat_history import ChatHistoryManager
from .handlers import MessageHandler

logger = logging.getLogger(__name__)


class TelegramClient:
    """Manages Telegram client lifecycle and configuration."""

    def __init__(self, workdir: str = "/Users/valorengels/src/ai"):
        self.client: Client | None = None
        self.workdir = workdir
        # Isolate Telegram session to prevent database conflicts with main system
        self.telegram_session_dir = os.path.join(workdir, "telegram_sessions")
        os.makedirs(self.telegram_session_dir, exist_ok=True)
        self._migrate_existing_session()
        self.chat_history = ChatHistoryManager()
        self.message_handler: MessageHandler | None = None
        self.bot_start_time = None
        self._active_handlers = set()  # Track active message handlers

    def _migrate_existing_session(self):
        """Migrate existing session file to isolated directory."""
        old_session_path = os.path.join(self.workdir, "ai_project_bot.session")
        new_session_path = os.path.join(self.telegram_session_dir, "ai_project_bot.session")
        
        if os.path.exists(old_session_path) and not os.path.exists(new_session_path):
            try:
                import shutil
                shutil.move(old_session_path, new_session_path)
                logger.info(f"Migrated Telegram session to isolated directory: {self.telegram_session_dir}")
            except Exception as e:
                logger.warning(f"Failed to migrate session file: {e}")

    async def initialize(self, notion_scout=None) -> bool:
        """Initialize the Telegram client with proper configuration."""
        try:
            self.bot_start_time = time.time()

            # Load existing chat history
            self.chat_history.load_history()

            api_id = os.getenv("TELEGRAM_API_ID")
            api_hash = os.getenv("TELEGRAM_API_HASH")

            logger.info(
                f"Loading Telegram credentials: api_id={api_id}, api_hash={'*' * len(api_hash) if api_hash else None}"
            )

            if not all([api_id, api_hash]):
                logger.error("Telegram credentials not found in environment variables")
                return False

            # Create client with isolated session storage to prevent database conflicts
            self.client = Client(
                "ai_project_bot",
                api_id=int(api_id),
                api_hash=api_hash,
                workdir=self.telegram_session_dir,  # Isolated session directory
                max_concurrent_transmissions=1,  # Reduce concurrent transmissions to prevent locks
                sleep_threshold=60,  # Prevent flood wait issues
                no_updates=False,  # Ensure we receive updates
            )

            # Start the client
            await self.client.start()
            logger.info("Telegram client started successfully")

            # Initialize unified message handler with Valor agent
            try:
                from agents.valor.agent import valor_agent
                agent_instance = valor_agent
            except ImportError:
                logger.warning("Could not import Valor agent, using None")
                agent_instance = None
                
            self.message_handler = MessageHandler(
                telegram_bot=self.client,
                valor_agent=agent_instance,
            )
            await self.message_handler.initialize()

            # Initialize new missed message system
            await self._initialize_missed_message_system()

            # Note: Missed message integration will be handled differently in the new system

            # Register message handler with active tracking
            @self.client.on_message()
            async def handle_message(client, message):
                handler_id = f"{message.chat.id}_{message.id}_{time.time()}"
                self._active_handlers.add(handler_id)

                try:
                    logger.debug(
                        f"DEBUG: Received message from {message.from_user.username if message.from_user else 'unknown'}: {message.text[:50] if message.text else 'non-text'}"
                    )
                    # Pass the client object so handler can access missed_message_integration
                    await self.message_handler.handle_message(client, message)
                finally:
                    # Always remove handler from active set, even if exception occurs
                    self._active_handlers.discard(handler_id)

            # Test message handling with self-ping
            await self._test_message_handling()

            return True

        except Exception as e:
            logger.error(f"Failed to start Telegram client: {e}")
            return False

    async def stop(self):
        """Stop the Telegram client and save state."""
        logger.info("Saving chat history...")
        self.chat_history.save_history()

        if self.client:
            try:
                await self.client.stop()
                logger.info("Telegram client stopped")
            except Exception as e:
                logger.error(f"Error stopping Telegram client: {e}")

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
                    "id": chat.id,
                    "title": getattr(chat, "title", None) or getattr(chat, "first_name", "Unknown"),
                    "type": chat.type.name,
                    "username": getattr(chat, "username", None),
                    "is_verified": getattr(chat, "is_verified", False),
                    "is_restricted": getattr(chat, "is_restricted", False),
                    "unread_count": dialog.unread_messages_count,
                    "last_message_date": dialog.top_message.date if dialog.top_message else None,
                }

                # Categorize based on chat type
                if chat.type in [ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL]:
                    # Add group-specific information
                    chat_info["member_count"] = getattr(chat, "members_count", None)
                    chat_info["description"] = getattr(chat, "description", None)
                    groups.append(chat_info)
                elif chat.type == ChatType.PRIVATE:
                    # Add DM-specific information
                    chat_info["last_name"] = getattr(chat, "last_name", None)
                    chat_info["phone_number"] = getattr(chat, "phone_number", None)
                    chat_info["is_contact"] = getattr(chat, "is_contact", False)
                    dms.append(chat_info)

            return {
                "groups": groups,
                "dms": dms,
                "total_groups": len(groups),
                "total_dms": len(dms),
                "total_dialogs": len(groups) + len(dms),
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

    async def _initialize_missed_message_system(self):
        """Initialize the new promise-based missed message system."""
        if not self.client or not self.message_handler:
            return

        try:
            from .missed_message_integration import MissedMessageIntegration

            # Initialize the new missed message system
            self.missed_message_integration = MissedMessageIntegration(
                self.client, self.message_handler
            )

            # Start background scanning (non-blocking)
            await self.missed_message_integration.startup_scan()

            logger.info("✅ New missed message system initialized successfully")

        except Exception as e:
            logger.error(f"❌ Error initializing missed message system: {e}")
            # Don't fail startup - continue without missed message detection
            self.missed_message_integration = None

    async def _test_message_handling(self):
        """Test message handling by sending a self-ping to verify the system works end-to-end."""
        if not self.client or not self.message_handler:
            return

        logger.info("🔄 Testing message handling with self-ping...")

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
                test_found = any(
                    test_message in msg.get("content", "") for msg in recent_messages[-3:]
                )

                if test_found:
                    logger.info("✅ Self-ping test successful - message handling is operational")
                else:
                    logger.warning(
                        "⚠️  Self-ping test: message sent but not processed (check whitelist)"
                    )
            else:
                logger.warning(
                    "⚠️  Self-ping test: no chat history found (check whitelist configuration)"
                )

        except Exception as e:
            logger.warning(f"⚠️  Self-ping test failed: {e}")
            logger.warning("   This indicates message handling may not be working properly")
            # Don't fail startup, but warn the user
