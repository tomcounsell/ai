"""
Telegram Tools MCP Server

This module implements MCP server for Telegram integration:
- Message sending and receiving
- Chat management and reactions
- Message history and search
- Presence and status management
- Bot functionality
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Union
import json

from telethon import TelegramClient, events
from telethon.tl.types import (
    Message, User, Chat, Channel, 
    MessageMediaPhoto, MessageMediaDocument,
    PeerUser, PeerChat, PeerChannel
)
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError
from pydantic import BaseModel, Field, validator

from .base import MCPServer, MCPToolCapability, MCPRequest, MCPError
from .context_manager import MCPContextManager, SecurityLevel


class TelegramMessage(BaseModel):
    """Telegram message structure."""
    
    id: int = Field(..., description="Message ID")
    chat_id: int = Field(..., description="Chat ID")
    from_id: Optional[int] = Field(None, description="Sender user ID")
    text: Optional[str] = Field(None, description="Message text")
    date: datetime = Field(..., description="Message timestamp")
    
    # Message metadata
    reply_to_msg_id: Optional[int] = Field(None, description="Reply to message ID")
    forward_from_id: Optional[int] = Field(None, description="Forwarded from user ID")
    edit_date: Optional[datetime] = Field(None, description="Edit timestamp")
    
    # Media information
    has_media: bool = Field(default=False, description="Whether message has media")
    media_type: Optional[str] = Field(None, description="Media type (photo, document, etc.)")
    
    # Message state
    is_outgoing: bool = Field(default=False, description="Whether message is outgoing")
    is_read: bool = Field(default=False, description="Whether message is read")
    is_pinned: bool = Field(default=False, description="Whether message is pinned")
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class TelegramChat(BaseModel):
    """Telegram chat structure."""
    
    id: int = Field(..., description="Chat ID")
    title: Optional[str] = Field(None, description="Chat title")
    username: Optional[str] = Field(None, description="Chat username")
    type: str = Field(..., description="Chat type (user, chat, channel)")
    
    # Chat metadata
    participants_count: Optional[int] = Field(None, description="Number of participants")
    is_broadcast: bool = Field(default=False, description="Whether chat is a broadcast channel")
    is_group: bool = Field(default=False, description="Whether chat is a group")
    is_private: bool = Field(default=False, description="Whether chat is private")
    
    # Access information
    can_send_messages: bool = Field(default=True, description="Can send messages to chat")
    is_admin: bool = Field(default=False, description="Whether user is admin")
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class TelegramUser(BaseModel):
    """Telegram user structure."""
    
    id: int = Field(..., description="User ID")
    username: Optional[str] = Field(None, description="Username")
    first_name: Optional[str] = Field(None, description="First name")
    last_name: Optional[str] = Field(None, description="Last name")
    phone: Optional[str] = Field(None, description="Phone number")
    
    # Status information
    is_self: bool = Field(default=False, description="Whether user is current user")
    is_contact: bool = Field(default=False, description="Whether user is in contacts")
    is_bot: bool = Field(default=False, description="Whether user is a bot")
    
    # Online status
    last_seen: Optional[datetime] = Field(None, description="Last seen timestamp")
    is_online: bool = Field(default=False, description="Whether user is currently online")
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class TelegramReaction(BaseModel):
    """Telegram message reaction structure."""
    
    message_id: int = Field(..., description="Message ID")
    chat_id: int = Field(..., description="Chat ID")
    user_id: int = Field(..., description="User who reacted")
    emoji: str = Field(..., description="Reaction emoji")
    added: bool = Field(..., description="Whether reaction was added (True) or removed (False)")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class TelegramToolsServer(MCPServer):
    """
    MCP Server implementation for Telegram integration.
    
    Provides stateless Telegram functionality with context injection including:
    - Message sending and receiving
    - Chat management and search
    - Message history retrieval
    - Presence and status management
    - Bot operations
    """
    
    def __init__(
        self,
        name: str = "telegram_tools",
        version: str = "1.0.0",
        description: str = "Telegram integration MCP server",
        api_id: str = None,
        api_hash: str = None,
        phone: str = None,
        bot_token: str = None,
        session_name: str = "mcp_telegram",
        **kwargs
    ):
        super().__init__(name, version, description, **kwargs)
        
        # Telegram API configuration
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone = phone
        self.bot_token = bot_token
        self.session_name = session_name
        
        # Telegram client
        self.client: Optional[TelegramClient] = None
        self.is_authenticated = False
        
        # Message handlers and state
        self._message_handlers: List[callable] = []
        self._reaction_handlers: List[callable] = []
        
        # In-memory stores for caching
        self._cached_chats: Dict[int, TelegramChat] = {}
        self._cached_users: Dict[int, TelegramUser] = {}
        self._message_cache: Dict[int, List[TelegramMessage]] = {}
        
        self.logger.info("Telegram Tools Server initialized")
    
    async def initialize(self) -> None:
        """Initialize the Telegram tools server."""
        try:
            if not self.api_id or not self.api_hash:
                raise MCPError(
                    "Telegram API ID and API Hash are required",
                    error_code="TELEGRAM_CONFIG_MISSING",
                    recoverable=False
                )
            
            # Initialize Telegram client
            if self.bot_token:
                # Bot mode
                self.client = TelegramClient(self.session_name, self.api_id, self.api_hash)
                await self.client.start(bot_token=self.bot_token)
                self.is_authenticated = True
                self.logger.info("Connected to Telegram as bot")
            elif self.phone:
                # User mode
                self.client = TelegramClient(self.session_name, self.api_id, self.api_hash)
                await self.client.start(phone=self.phone)
                self.is_authenticated = True
                self.logger.info("Connected to Telegram as user")
            else:
                self.logger.warning("No bot token or phone provided - some features may be limited")
            
            # Register event handlers
            if self.client and self.is_authenticated:
                await self._register_event_handlers()
            
            # Register tool capabilities
            await self._register_message_tools()
            await self._register_chat_tools()
            await self._register_user_tools()
            await self._register_history_tools()
            await self._register_presence_tools()
            
            self.logger.info("Telegram Tools Server initialized successfully")
            
        except Exception as e:
            self.logger.error(f"Failed to initialize Telegram Tools Server: {str(e)}")
            raise MCPError(
                f"Telegram server initialization failed: {str(e)}",
                error_code="TELEGRAM_INIT_ERROR",
                details={"error": str(e)},
                recoverable=False
            )
    
    async def shutdown(self) -> None:
        """Shutdown the Telegram tools server."""
        try:
            if self.client:
                await self.client.disconnect()
            self.logger.info("Telegram Tools Server shut down successfully")
            
        except Exception as e:
            self.logger.error(f"Error during Telegram Tools Server shutdown: {str(e)}")
    
    async def _register_event_handlers(self) -> None:
        """Register Telegram event handlers."""
        if not self.client:
            return
        
        @self.client.on(events.NewMessage)
        async def handle_new_message(event):
            """Handle new messages."""
            try:
                message = await self._convert_message(event.message)
                self.logger.debug(f"Received new message: {message.id}")
                
                # Cache message
                chat_id = message.chat_id
                if chat_id not in self._message_cache:
                    self._message_cache[chat_id] = []
                self._message_cache[chat_id].append(message)
                
                # Limit cache size
                if len(self._message_cache[chat_id]) > 1000:
                    self._message_cache[chat_id] = self._message_cache[chat_id][-500:]
                
                # Call registered handlers
                for handler in self._message_handlers:
                    try:
                        await handler(message)
                    except Exception as e:
                        self.logger.error(f"Message handler error: {str(e)}")
                        
            except Exception as e:
                self.logger.error(f"Error handling new message: {str(e)}")
        
        self.logger.info("Registered Telegram event handlers")
    
    # Message Tools
    
    async def _register_message_tools(self) -> None:
        """Register message-related tool capabilities."""
        
        # Send message
        send_message_capability = MCPToolCapability(
            name="telegram_send_message",
            description="Send a message to a Telegram chat",
            parameters={
                "chat_id": {"type": "string", "required": True, "description": "Chat ID or username"},
                "text": {"type": "string", "required": True, "description": "Message text"},
                "reply_to": {"type": "integer", "required": False, "description": "Message ID to reply to"},
                "parse_mode": {"type": "string", "required": False, "enum": ["markdown", "html"], "description": "Text parse mode"}
            },
            returns={"type": "object", "description": "Sent message"},
            tags=["telegram", "messages", "send"]
        )
        self.register_tool(send_message_capability, self._handle_send_message)
        
        # Edit message
        edit_message_capability = MCPToolCapability(
            name="telegram_edit_message",
            description="Edit a Telegram message",
            parameters={
                "chat_id": {"type": "string", "required": True, "description": "Chat ID or username"},
                "message_id": {"type": "integer", "required": True, "description": "Message ID to edit"},
                "text": {"type": "string", "required": True, "description": "New message text"}
            },
            returns={"type": "object", "description": "Edited message"},
            tags=["telegram", "messages", "edit"]
        )
        self.register_tool(edit_message_capability, self._handle_edit_message)
        
        # Delete message
        delete_message_capability = MCPToolCapability(
            name="telegram_delete_message",
            description="Delete a Telegram message",
            parameters={
                "chat_id": {"type": "string", "required": True, "description": "Chat ID or username"},
                "message_id": {"type": "integer", "required": True, "description": "Message ID to delete"}
            },
            returns={"type": "object", "properties": {"success": "boolean", "message_id": "integer"}},
            tags=["telegram", "messages", "delete"]
        )
        self.register_tool(delete_message_capability, self._handle_delete_message)
        
        # Forward message
        forward_message_capability = MCPToolCapability(
            name="telegram_forward_message",
            description="Forward a Telegram message to another chat",
            parameters={
                "from_chat_id": {"type": "string", "required": True, "description": "Source chat ID or username"},
                "to_chat_id": {"type": "string", "required": True, "description": "Destination chat ID or username"},
                "message_id": {"type": "integer", "required": True, "description": "Message ID to forward"}
            },
            returns={"type": "object", "description": "Forwarded message"},
            tags=["telegram", "messages", "forward"]
        )
        self.register_tool(forward_message_capability, self._handle_forward_message)
    
    async def _handle_send_message(self, request: MCPRequest, context: Dict[str, Any]) -> Dict[str, Any]:
        """Handle send message requests."""
        if not self.client or not self.is_authenticated:
            raise MCPError(
                "Telegram client not authenticated",
                error_code="TELEGRAM_NOT_AUTHENTICATED",
                request_id=request.id
            )
        
        chat_id = request.params.get("chat_id")
        text = request.params.get("text")
        reply_to = request.params.get("reply_to")
        parse_mode = request.params.get("parse_mode")
        
        if not chat_id or not text:
            raise MCPError(
                "Chat ID and text are required",
                error_code="MISSING_MESSAGE_PARAMS",
                request_id=request.id
            )
        
        try:
            # Convert chat_id to proper format
            entity = await self._resolve_entity(chat_id)
            
            # Send message
            message = await self.client.send_message(
                entity,
                text,
                reply_to=reply_to,
                parse_mode=parse_mode
            )
            
            # Convert to our format
            telegram_message = await self._convert_message(message)
            
            self.logger.info(f"Sent message to {chat_id}: {message.id}")
            
            return telegram_message.dict()
            
        except Exception as e:
            self.logger.error(f"Failed to send message: {str(e)}")
            raise MCPError(
                f"Failed to send message: {str(e)}",
                error_code="TELEGRAM_SEND_MESSAGE_ERROR",
                details={"chat_id": chat_id, "error": str(e)},
                request_id=request.id
            )
    
    async def _handle_edit_message(self, request: MCPRequest, context: Dict[str, Any]) -> Dict[str, Any]:
        """Handle edit message requests."""
        if not self.client or not self.is_authenticated:
            raise MCPError(
                "Telegram client not authenticated",
                error_code="TELEGRAM_NOT_AUTHENTICATED",
                request_id=request.id
            )
        
        chat_id = request.params.get("chat_id")
        message_id = request.params.get("message_id")
        text = request.params.get("text")
        
        if not chat_id or not message_id or not text:
            raise MCPError(
                "Chat ID, message ID, and text are required",
                error_code="MISSING_EDIT_PARAMS",
                request_id=request.id
            )
        
        try:
            # Convert chat_id to proper format
            entity = await self._resolve_entity(chat_id)
            
            # Edit message
            message = await self.client.edit_message(entity, message_id, text)
            
            # Convert to our format
            telegram_message = await self._convert_message(message)
            
            self.logger.info(f"Edited message {message_id} in {chat_id}")
            
            return telegram_message.dict()
            
        except Exception as e:
            self.logger.error(f"Failed to edit message: {str(e)}")
            raise MCPError(
                f"Failed to edit message: {str(e)}",
                error_code="TELEGRAM_EDIT_MESSAGE_ERROR",
                details={"chat_id": chat_id, "message_id": message_id, "error": str(e)},
                request_id=request.id
            )
    
    async def _handle_delete_message(self, request: MCPRequest, context: Dict[str, Any]) -> Dict[str, Any]:
        """Handle delete message requests."""
        if not self.client or not self.is_authenticated:
            raise MCPError(
                "Telegram client not authenticated",
                error_code="TELEGRAM_NOT_AUTHENTICATED",
                request_id=request.id
            )
        
        chat_id = request.params.get("chat_id")
        message_id = request.params.get("message_id")
        
        if not chat_id or not message_id:
            raise MCPError(
                "Chat ID and message ID are required",
                error_code="MISSING_DELETE_PARAMS",
                request_id=request.id
            )
        
        try:
            # Convert chat_id to proper format
            entity = await self._resolve_entity(chat_id)
            
            # Delete message
            await self.client.delete_messages(entity, message_id)
            
            self.logger.info(f"Deleted message {message_id} from {chat_id}")
            
            return {
                "success": True,
                "message_id": message_id
            }
            
        except Exception as e:
            self.logger.error(f"Failed to delete message: {str(e)}")
            raise MCPError(
                f"Failed to delete message: {str(e)}",
                error_code="TELEGRAM_DELETE_MESSAGE_ERROR",
                details={"chat_id": chat_id, "message_id": message_id, "error": str(e)},
                request_id=request.id
            )
    
    async def _handle_forward_message(self, request: MCPRequest, context: Dict[str, Any]) -> Dict[str, Any]:
        """Handle forward message requests."""
        if not self.client or not self.is_authenticated:
            raise MCPError(
                "Telegram client not authenticated",
                error_code="TELEGRAM_NOT_AUTHENTICATED",
                request_id=request.id
            )
        
        from_chat_id = request.params.get("from_chat_id")
        to_chat_id = request.params.get("to_chat_id")
        message_id = request.params.get("message_id")
        
        if not from_chat_id or not to_chat_id or not message_id:
            raise MCPError(
                "From chat ID, to chat ID, and message ID are required",
                error_code="MISSING_FORWARD_PARAMS",
                request_id=request.id
            )
        
        try:
            # Convert chat_ids to proper format
            from_entity = await self._resolve_entity(from_chat_id)
            to_entity = await self._resolve_entity(to_chat_id)
            
            # Forward message
            messages = await self.client.forward_messages(to_entity, message_id, from_entity)
            
            if messages:
                # Convert to our format
                telegram_message = await self._convert_message(messages[0])
                
                self.logger.info(f"Forwarded message {message_id} from {from_chat_id} to {to_chat_id}")
                
                return telegram_message.dict()
            else:
                raise MCPError(
                    "Failed to forward message",
                    error_code="TELEGRAM_FORWARD_FAILED",
                    request_id=request.id
                )
            
        except Exception as e:
            self.logger.error(f"Failed to forward message: {str(e)}")
            raise MCPError(
                f"Failed to forward message: {str(e)}",
                error_code="TELEGRAM_FORWARD_MESSAGE_ERROR",
                details={
                    "from_chat_id": from_chat_id,
                    "to_chat_id": to_chat_id,
                    "message_id": message_id,
                    "error": str(e)
                },
                request_id=request.id
            )
    
    # Chat Tools
    
    async def _register_chat_tools(self) -> None:
        """Register chat-related tool capabilities."""
        
        # List chats
        list_chats_capability = MCPToolCapability(
            name="telegram_list_chats",
            description="List Telegram chats/dialogs",
            parameters={
                "limit": {"type": "integer", "required": False, "default": 50, "description": "Maximum number of chats"},
                "chat_type": {"type": "string", "required": False, "enum": ["all", "users", "groups", "channels"], "description": "Filter by chat type"}
            },
            returns={"type": "array", "items": "TelegramChat"},
            tags=["telegram", "chats", "list"]
        )
        self.register_tool(list_chats_capability, self._handle_list_chats)
        
        # Get chat info
        get_chat_capability = MCPToolCapability(
            name="telegram_get_chat",
            description="Get detailed information about a Telegram chat",
            parameters={
                "chat_id": {"type": "string", "required": True, "description": "Chat ID or username"}
            },
            returns={"type": "object", "description": "Chat information"},
            tags=["telegram", "chats", "info"]
        )
        self.register_tool(get_chat_capability, self._handle_get_chat)
    
    async def _handle_list_chats(self, request: MCPRequest, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Handle list chats requests."""
        if not self.client or not self.is_authenticated:
            raise MCPError(
                "Telegram client not authenticated",
                error_code="TELEGRAM_NOT_AUTHENTICATED",
                request_id=request.id
            )
        
        limit = request.params.get("limit", 50)
        chat_type = request.params.get("chat_type", "all")
        
        try:
            chats = []
            async for dialog in self.client.iter_dialogs(limit=limit):
                try:
                    # Filter by chat type
                    if chat_type != "all":
                        if chat_type == "users" and not dialog.is_user:
                            continue
                        elif chat_type == "groups" and not dialog.is_group:
                            continue
                        elif chat_type == "channels" and not dialog.is_channel:
                            continue
                    
                    chat = await self._convert_chat(dialog)
                    chats.append(chat)
                    
                    # Cache chat
                    self._cached_chats[chat.id] = chat
                    
                except Exception as e:
                    self.logger.warning(f"Error converting dialog: {str(e)}")
                    continue
            
            self.logger.info(f"Listed {len(chats)} Telegram chats")
            
            return [chat.dict() for chat in chats]
            
        except Exception as e:
            self.logger.error(f"Failed to list chats: {str(e)}")
            raise MCPError(
                f"Failed to list chats: {str(e)}",
                error_code="TELEGRAM_LIST_CHATS_ERROR",
                details={"error": str(e)},
                request_id=request.id
            )
    
    async def _handle_get_chat(self, request: MCPRequest, context: Dict[str, Any]) -> Dict[str, Any]:
        """Handle get chat requests."""
        if not self.client or not self.is_authenticated:
            raise MCPError(
                "Telegram client not authenticated",
                error_code="TELEGRAM_NOT_AUTHENTICATED",
                request_id=request.id
            )
        
        chat_id = request.params.get("chat_id")
        
        if not chat_id:
            raise MCPError(
                "Chat ID is required",
                error_code="MISSING_CHAT_ID",
                request_id=request.id
            )
        
        try:
            # Check cache first
            try:
                chat_id_int = int(chat_id)
                if chat_id_int in self._cached_chats:
                    return self._cached_chats[chat_id_int].dict()
            except ValueError:
                pass  # Not an integer, will resolve by username
            
            # Get chat entity
            entity = await self._resolve_entity(chat_id)
            
            # Get full info
            if hasattr(entity, 'id'):
                # Create dialog-like object for conversion
                class DialogLike:
                    def __init__(self, entity):
                        self.entity = entity
                        self.id = entity.id
                        self.title = getattr(entity, 'title', None)
                        self.username = getattr(entity, 'username', None)
                    
                    @property
                    def is_user(self):
                        return isinstance(self.entity, User)
                    
                    @property
                    def is_group(self):
                        return isinstance(self.entity, Chat)
                    
                    @property
                    def is_channel(self):
                        return isinstance(self.entity, Channel)
                
                dialog_like = DialogLike(entity)
                chat = await self._convert_chat(dialog_like)
                
                # Cache chat
                self._cached_chats[chat.id] = chat
                
                self.logger.info(f"Retrieved Telegram chat: {chat_id}")
                
                return chat.dict()
            else:
                raise MCPError(
                    f"Invalid chat entity: {chat_id}",
                    error_code="INVALID_CHAT_ENTITY",
                    request_id=request.id
                )
            
        except Exception as e:
            self.logger.error(f"Failed to get chat: {str(e)}")
            raise MCPError(
                f"Failed to get chat: {str(e)}",
                error_code="TELEGRAM_GET_CHAT_ERROR",
                details={"chat_id": chat_id, "error": str(e)},
                request_id=request.id
            )
    
    # Message History Tools
    
    async def _register_history_tools(self) -> None:
        """Register message history tool capabilities."""
        
        # Get message history
        get_history_capability = MCPToolCapability(
            name="telegram_get_message_history",
            description="Get message history from a Telegram chat",
            parameters={
                "chat_id": {"type": "string", "required": True, "description": "Chat ID or username"},
                "limit": {"type": "integer", "required": False, "default": 50, "description": "Maximum number of messages"},
                "offset_id": {"type": "integer", "required": False, "description": "Offset message ID for pagination"},
                "search": {"type": "string", "required": False, "description": "Search query for messages"}
            },
            returns={"type": "array", "items": "TelegramMessage"},
            tags=["telegram", "messages", "history"]
        )
        self.register_tool(get_history_capability, self._handle_get_message_history)
    
    async def _handle_get_message_history(self, request: MCPRequest, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Handle get message history requests."""
        if not self.client or not self.is_authenticated:
            raise MCPError(
                "Telegram client not authenticated",
                error_code="TELEGRAM_NOT_AUTHENTICATED",
                request_id=request.id
            )
        
        chat_id = request.params.get("chat_id")
        limit = request.params.get("limit", 50)
        offset_id = request.params.get("offset_id")
        search = request.params.get("search")
        
        if not chat_id:
            raise MCPError(
                "Chat ID is required",
                error_code="MISSING_CHAT_ID",
                request_id=request.id
            )
        
        try:
            # Convert chat_id to proper format
            entity = await self._resolve_entity(chat_id)
            
            messages = []
            
            if search:
                # Search messages
                async for message in self.client.iter_messages(
                    entity,
                    limit=limit,
                    search=search,
                    offset_id=offset_id
                ):
                    telegram_message = await self._convert_message(message)
                    messages.append(telegram_message)
            else:
                # Get message history
                async for message in self.client.iter_messages(
                    entity,
                    limit=limit,
                    offset_id=offset_id
                ):
                    telegram_message = await self._convert_message(message)
                    messages.append(telegram_message)
            
            self.logger.info(f"Retrieved {len(messages)} messages from {chat_id}")
            
            return [msg.dict() for msg in messages]
            
        except Exception as e:
            self.logger.error(f"Failed to get message history: {str(e)}")
            raise MCPError(
                f"Failed to get message history: {str(e)}",
                error_code="TELEGRAM_GET_HISTORY_ERROR",
                details={"chat_id": chat_id, "error": str(e)},
                request_id=request.id
            )
    
    # User and Presence Tools
    
    async def _register_user_tools(self) -> None:
        """Register user-related tool capabilities."""
        
        # Get user info
        get_user_capability = MCPToolCapability(
            name="telegram_get_user",
            description="Get information about a Telegram user",
            parameters={
                "user_id": {"type": "string", "required": True, "description": "User ID or username"}
            },
            returns={"type": "object", "description": "User information"},
            tags=["telegram", "users", "info"]
        )
        self.register_tool(get_user_capability, self._handle_get_user)
    
    async def _register_presence_tools(self) -> None:
        """Register presence-related tool capabilities."""
        
        # Get online status
        get_status_capability = MCPToolCapability(
            name="telegram_get_user_status",
            description="Get online status of a Telegram user",
            parameters={
                "user_id": {"type": "string", "required": True, "description": "User ID or username"}
            },
            returns={"type": "object", "properties": {"user_id": "string", "is_online": "boolean", "last_seen": "string"}},
            tags=["telegram", "users", "presence"]
        )
        self.register_tool(get_status_capability, self._handle_get_user_status)
    
    async def _handle_get_user(self, request: MCPRequest, context: Dict[str, Any]) -> Dict[str, Any]:
        """Handle get user requests."""
        if not self.client or not self.is_authenticated:
            raise MCPError(
                "Telegram client not authenticated",
                error_code="TELEGRAM_NOT_AUTHENTICATED",
                request_id=request.id
            )
        
        user_id = request.params.get("user_id")
        
        if not user_id:
            raise MCPError(
                "User ID is required",
                error_code="MISSING_USER_ID",
                request_id=request.id
            )
        
        try:
            # Get user entity
            entity = await self._resolve_entity(user_id)
            
            if isinstance(entity, User):
                user = await self._convert_user(entity)
                
                # Cache user
                self._cached_users[user.id] = user
                
                self.logger.info(f"Retrieved Telegram user: {user_id}")
                
                return user.dict()
            else:
                raise MCPError(
                    f"Entity is not a user: {user_id}",
                    error_code="NOT_A_USER",
                    request_id=request.id
                )
            
        except Exception as e:
            self.logger.error(f"Failed to get user: {str(e)}")
            raise MCPError(
                f"Failed to get user: {str(e)}",
                error_code="TELEGRAM_GET_USER_ERROR",
                details={"user_id": user_id, "error": str(e)},
                request_id=request.id
            )
    
    async def _handle_get_user_status(self, request: MCPRequest, context: Dict[str, Any]) -> Dict[str, Any]:
        """Handle get user status requests."""
        if not self.client or not self.is_authenticated:
            raise MCPError(
                "Telegram client not authenticated",
                error_code="TELEGRAM_NOT_AUTHENTICATED",
                request_id=request.id
            )
        
        user_id = request.params.get("user_id")
        
        if not user_id:
            raise MCPError(
                "User ID is required",
                error_code="MISSING_USER_ID",
                request_id=request.id
            )
        
        try:
            # Get user entity
            entity = await self._resolve_entity(user_id)
            
            if isinstance(entity, User):
                # Determine online status
                is_online = False
                last_seen = None
                
                if hasattr(entity, 'status') and entity.status:
                    from telethon.tl.types import UserStatusOnline, UserStatusOffline, UserStatusRecently
                    
                    if isinstance(entity.status, UserStatusOnline):
                        is_online = True
                    elif isinstance(entity.status, UserStatusOffline):
                        last_seen = entity.status.was_online
                    elif isinstance(entity.status, UserStatusRecently):
                        is_online = False  # Recently online but not now
                
                result = {
                    "user_id": str(entity.id),
                    "is_online": is_online,
                    "last_seen": last_seen.isoformat() if last_seen else None
                }
                
                self.logger.info(f"Retrieved user status for: {user_id}")
                
                return result
            else:
                raise MCPError(
                    f"Entity is not a user: {user_id}",
                    error_code="NOT_A_USER",
                    request_id=request.id
                )
            
        except Exception as e:
            self.logger.error(f"Failed to get user status: {str(e)}")
            raise MCPError(
                f"Failed to get user status: {str(e)}",
                error_code="TELEGRAM_GET_USER_STATUS_ERROR",
                details={"user_id": user_id, "error": str(e)},
                request_id=request.id
            )
    
    # Helper methods
    
    async def _resolve_entity(self, identifier: str):
        """Resolve entity from ID or username."""
        if not self.client:
            raise MCPError("Telegram client not available", error_code="TELEGRAM_CLIENT_MISSING")
        
        try:
            # Try to convert to integer (ID)
            try:
                entity_id = int(identifier)
                return await self.client.get_entity(entity_id)
            except ValueError:
                # It's a username
                return await self.client.get_entity(identifier)
        except Exception as e:
            raise MCPError(
                f"Failed to resolve entity '{identifier}': {str(e)}",
                error_code="ENTITY_RESOLUTION_ERROR",
                details={"identifier": identifier, "error": str(e)}
            )
    
    async def _convert_message(self, message: Message) -> TelegramMessage:
        """Convert Telethon message to our format."""
        # Determine media information
        has_media = bool(message.media)
        media_type = None
        
        if has_media:
            if isinstance(message.media, MessageMediaPhoto):
                media_type = "photo"
            elif isinstance(message.media, MessageMediaDocument):
                media_type = "document"
            else:
                media_type = "other"
        
        return TelegramMessage(
            id=message.id,
            chat_id=message.peer_id.user_id if hasattr(message.peer_id, 'user_id') else (
                message.peer_id.chat_id if hasattr(message.peer_id, 'chat_id') else
                message.peer_id.channel_id
            ),
            from_id=message.from_id.user_id if message.from_id else None,
            text=message.message or "",
            date=message.date,
            reply_to_msg_id=message.reply_to.reply_to_msg_id if message.reply_to else None,
            edit_date=message.edit_date,
            has_media=has_media,
            media_type=media_type,
            is_outgoing=message.out,
            is_read=not message.unread,
            is_pinned=message.pinned
        )
    
    async def _convert_chat(self, dialog) -> TelegramChat:
        """Convert dialog to our chat format."""
        entity = dialog.entity
        
        # Determine chat type
        chat_type = "unknown"
        if isinstance(entity, User):
            chat_type = "user"
        elif isinstance(entity, Chat):
            chat_type = "chat"
        elif isinstance(entity, Channel):
            chat_type = "channel"
        
        # Get title
        title = None
        if hasattr(entity, 'title'):
            title = entity.title
        elif hasattr(entity, 'first_name'):
            title = entity.first_name
            if hasattr(entity, 'last_name') and entity.last_name:
                title += f" {entity.last_name}"
        
        return TelegramChat(
            id=entity.id,
            title=title,
            username=getattr(entity, 'username', None),
            type=chat_type,
            participants_count=getattr(entity, 'participants_count', None),
            is_broadcast=getattr(entity, 'broadcast', False),
            is_group=isinstance(entity, Chat),
            is_private=isinstance(entity, User)
        )
    
    async def _convert_user(self, user: User) -> TelegramUser:
        """Convert Telethon user to our format."""
        # Determine online status
        is_online = False
        last_seen = None
        
        if hasattr(user, 'status') and user.status:
            from telethon.tl.types import UserStatusOnline, UserStatusOffline
            
            if isinstance(user.status, UserStatusOnline):
                is_online = True
            elif isinstance(user.status, UserStatusOffline):
                last_seen = user.status.was_online
        
        return TelegramUser(
            id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            phone=user.phone,
            is_self=user.is_self,
            is_contact=user.contact,
            is_bot=user.bot,
            last_seen=last_seen,
            is_online=is_online
        )


# Export the server class
__all__ = ["TelegramToolsServer"]