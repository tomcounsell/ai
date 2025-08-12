"""Telegram Client Implementation

This module provides a robust Telegram client using Telethon with
comprehensive event handling, session management, graceful shutdown,
reconnection logic, and message queue reliability.
"""

import asyncio
import logging
import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
from enum import Enum

from telethon import TelegramClient as TelethonClient, events
from telethon.errors import (
    SessionPasswordNeededError, PhoneCodeInvalidError,
    PhoneNumberInvalidError, FloodWaitError, AuthKeyError,
    ConnectionError as TelegramConnectionError
)
from telethon.tl.types import Message, User, Chat, Channel, Updates

from .unified_processor import UnifiedProcessor, ProcessingRequest, ProcessingResult
from .components.response_manager import FormattedResponse


logger = logging.getLogger(__name__)


class ClientStatus(Enum):
    """Client connection status"""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    AUTHENTICATING = "authenticating"
    AUTHENTICATED = "authenticated"
    ERROR = "error"
    SHUTTING_DOWN = "shutting_down"


class MessagePriority(Enum):
    """Message delivery priority"""
    CRITICAL = 0    # System messages, errors
    HIGH = 1        # Commands, urgent responses  
    NORMAL = 2      # Regular conversation
    LOW = 3         # Background tasks, analytics


@dataclass
class OutboundMessage:
    """Outbound message for delivery queue"""
    chat_id: int
    text: str
    priority: MessagePriority = MessagePriority.NORMAL
    parse_mode: Optional[str] = None
    reply_to_message_id: Optional[int] = None
    disable_web_page_preview: bool = False
    disable_notification: bool = False
    media_attachments: List[Any] = field(default_factory=list)
    retry_count: int = 0
    max_retries: int = 3
    created_at: float = field(default_factory=time.time)
    scheduled_at: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ConnectionMetrics:
    """Connection performance metrics"""
    connect_time: float = 0.0
    total_connections: int = 0
    reconnection_count: int = 0
    last_disconnect_time: float = 0.0
    uptime_seconds: float = 0.0
    message_send_count: int = 0
    message_receive_count: int = 0
    error_count: int = 0
    flood_wait_count: int = 0


class TelegramClient:
    """
    Advanced Telegram client with robust connection management,
    event handling, message queuing, and comprehensive error recovery.
    """
    
    def __init__(
        self,
        session_name: str,
        api_id: int,
        api_hash: str,
        phone_number: Optional[str] = None,
        processor: Optional[UnifiedProcessor] = None,
        auto_reconnect: bool = True,
        max_reconnect_attempts: int = 10,
        reconnect_delay: int = 5,
        message_queue_size: int = 1000,
        enable_flood_protection: bool = True,
        rate_limit_messages_per_second: int = 30,
        device_model: str = "AI Rebuild Client",
        system_version: str = "1.0.0"
    ):
        """
        Initialize the Telegram client.
        
        Args:
            session_name: Name for the session file
            api_id: Telegram API ID
            api_hash: Telegram API hash
            phone_number: Phone number for authentication
            processor: Message processor instance
            auto_reconnect: Enable automatic reconnection
            max_reconnect_attempts: Maximum reconnection attempts
            reconnect_delay: Delay between reconnection attempts
            message_queue_size: Maximum size of outbound message queue
            enable_flood_protection: Enable flood wait protection
            rate_limit_messages_per_second: Rate limit for outbound messages
            device_model: Device model for session
            system_version: System version for session
        """
        self.session_name = session_name
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone_number = phone_number
        self.processor = processor or UnifiedProcessor()
        
        # Connection management
        self.auto_reconnect = auto_reconnect
        self.max_reconnect_attempts = max_reconnect_attempts
        self.reconnect_delay = reconnect_delay
        self.reconnect_attempts = 0
        self.status = ClientStatus.DISCONNECTED
        
        # Message queuing
        self.outbound_queue: Dict[MessagePriority, deque] = {
            priority: deque(maxlen=message_queue_size // 4)
            for priority in MessagePriority
        }
        self.queue_processors: Dict[MessagePriority, asyncio.Task] = {}
        
        # Rate limiting
        self.enable_flood_protection = enable_flood_protection
        self.rate_limit = rate_limit_messages_per_second
        self.message_timestamps: deque = deque(maxlen=rate_limit_messages_per_second)
        
        # Event handlers
        self.event_handlers: Dict[str, List[Callable]] = defaultdict(list)
        self.message_handlers: List[Callable] = []
        
        # Performance tracking
        self.metrics = ConnectionMetrics()
        self.start_time = time.time()
        
        # Initialize Telethon client
        self.client = TelethonClient(
            session_name,
            api_id,
            api_hash,
            device_model=device_model,
            system_version=system_version,
            timeout=30,
            retry_delay=1,
            auto_reconnect=auto_reconnect
        )
        
        # State management
        self._shutdown_event = asyncio.Event()
        self._processing_tasks: Set[asyncio.Task] = set()
        self._connected_event = asyncio.Event()
        
        # Setup event handlers
        self._setup_event_handlers()
        
        logger.info(
            f"TelegramClient initialized: session={session_name}, "
            f"auto_reconnect={auto_reconnect}, rate_limit={rate_limit_messages_per_second}/s"
        )
    
    async def connect(self, phone_code: Optional[str] = None, password: Optional[str] = None) -> bool:
        """
        Connect and authenticate with Telegram.
        
        Args:
            phone_code: Phone verification code if needed
            password: 2FA password if needed
            
        Returns:
            True if connection successful, False otherwise
        """
        try:
            self.status = ClientStatus.CONNECTING
            connect_start = time.perf_counter()
            
            logger.info("Connecting to Telegram...")
            
            # Connect to Telegram
            await self.client.connect()
            
            if not await self.client.is_user_authorized():
                self.status = ClientStatus.AUTHENTICATING
                logger.info("User not authorized, starting authentication...")
                
                if not self.phone_number:
                    logger.error("Phone number required for authentication")
                    self.status = ClientStatus.ERROR
                    return False
                
                # Send phone code
                try:
                    await self.client.send_code_request(self.phone_number)
                    logger.info("Phone code sent")
                    
                    if phone_code:
                        await self.client.sign_in(self.phone_number, phone_code)
                        logger.info("Phone code verification successful")
                    else:
                        logger.warning("Phone code required for authentication")
                        self.status = ClientStatus.AUTHENTICATING
                        return False
                        
                except SessionPasswordNeededError:
                    if password:
                        await self.client.sign_in(password=password)
                        logger.info("2FA password verification successful")
                    else:
                        logger.warning("2FA password required")
                        self.status = ClientStatus.AUTHENTICATING
                        return False
                        
                except (PhoneCodeInvalidError, PhoneNumberInvalidError) as e:
                    logger.error(f"Authentication error: {e}")
                    self.status = ClientStatus.ERROR
                    return False
            
            # Connection successful
            self.status = ClientStatus.AUTHENTICATED
            self.metrics.connect_time = time.perf_counter() - connect_start
            self.metrics.total_connections += 1
            self.reconnect_attempts = 0
            
            # Start message queue processors
            await self._start_queue_processors()
            
            # Set connected event
            self._connected_event.set()
            
            # Get client info
            me = await self.client.get_me()
            logger.info(
                f"Successfully connected as {me.first_name} ({me.username}) "
                f"in {self.metrics.connect_time:.2f}s"
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Connection failed: {str(e)}", exc_info=True)
            self.status = ClientStatus.ERROR
            self.metrics.error_count += 1
            return False
    
    async def disconnect(self) -> None:
        """Gracefully disconnect from Telegram"""
        
        logger.info("Disconnecting from Telegram...")
        self.status = ClientStatus.SHUTTING_DOWN
        
        # Signal shutdown
        self._shutdown_event.set()
        self._connected_event.clear()
        
        # Stop queue processors
        await self._stop_queue_processors()
        
        # Wait for active processing tasks
        if self._processing_tasks:
            logger.info(f"Waiting for {len(self._processing_tasks)} processing tasks...")
            await asyncio.gather(*self._processing_tasks, return_exceptions=True)
        
        # Disconnect client
        if self.client.is_connected():
            await self.client.disconnect()
            self.metrics.last_disconnect_time = time.time()
        
        self.status = ClientStatus.DISCONNECTED
        logger.info("Telegram client disconnected")
    
    async def send_message(
        self,
        response: FormattedResponse,
        priority: MessagePriority = MessagePriority.NORMAL
    ) -> bool:
        """
        Queue a message for sending.
        
        Args:
            response: Formatted response to send
            priority: Message priority
            
        Returns:
            True if queued successfully, False otherwise
        """
        try:
            # Create outbound message
            message = OutboundMessage(
                chat_id=response.chat_id,
                text=response.text,
                priority=priority,
                parse_mode=response.parse_mode,
                reply_to_message_id=response.reply_to_message_id,
                disable_web_page_preview=response.disable_web_page_preview,
                disable_notification=response.disable_notification,
                media_attachments=response.media_attachments,
                metadata=response.metadata
            )
            
            # Add to appropriate priority queue
            if len(self.outbound_queue[priority]) >= self.outbound_queue[priority].maxlen:
                # Queue full, drop oldest message
                dropped = self.outbound_queue[priority].popleft()
                logger.warning(f"Dropped message due to full queue: {dropped.chat_id}")
            
            self.outbound_queue[priority].append(message)
            
            logger.debug(
                f"Queued message for chat {response.chat_id}, "
                f"priority: {priority.name}, queue_size: {len(self.outbound_queue[priority])}"
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to queue message: {str(e)}")
            return False
    
    async def send_messages_batch(
        self,
        responses: List[FormattedResponse],
        priority: MessagePriority = MessagePriority.NORMAL
    ) -> int:
        """
        Queue multiple messages for sending.
        
        Args:
            responses: List of formatted responses
            priority: Message priority
            
        Returns:
            Number of messages successfully queued
        """
        queued_count = 0
        
        for response in responses:
            if await self.send_message(response, priority):
                queued_count += 1
        
        logger.debug(f"Queued {queued_count}/{len(responses)} messages")
        return queued_count
    
    def add_message_handler(self, handler: Callable) -> None:
        """Add a message handler function"""
        self.message_handlers.append(handler)
        logger.debug(f"Added message handler: {handler.__name__}")
    
    def add_event_handler(self, event_type: str, handler: Callable) -> None:
        """Add an event handler function"""
        self.event_handlers[event_type].append(handler)
        logger.debug(f"Added {event_type} event handler: {handler.__name__}")
    
    async def run_until_disconnected(self) -> None:
        """Run the client until disconnected"""
        
        logger.info("Starting client event loop...")
        
        try:
            await self._connected_event.wait()
            
            # Run until shutdown
            await self._shutdown_event.wait()
            
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt")
        except Exception as e:
            logger.error(f"Client loop error: {str(e)}", exc_info=True)
        
        finally:
            await self.disconnect()
    
    def _setup_event_handlers(self) -> None:
        """Setup Telethon event handlers"""
        
        @self.client.on(events.NewMessage)
        async def handle_new_message(event):
            await self._handle_new_message(event)
        
        @self.client.on(events.MessageEdited)
        async def handle_message_edited(event):
            await self._handle_message_edited(event)
        
        @self.client.on(events.MessageDeleted)
        async def handle_message_deleted(event):
            await self._handle_message_deleted(event)
        
        @self.client.on(events.ChatAction)
        async def handle_chat_action(event):
            await self._handle_chat_action(event)
    
    async def _handle_new_message(self, event) -> None:
        """Handle incoming new message"""
        
        try:
            self.metrics.message_receive_count += 1
            
            message = event.message
            sender = await event.get_sender()
            
            # Skip messages from self
            if sender and hasattr(sender, 'id'):
                me = await self.client.get_me()
                if sender.id == me.id:
                    return
            
            # Create processing request
            processing_request = ProcessingRequest(
                message=message,
                user=sender,
                chat_id=message.peer_id.chat_id if hasattr(message.peer_id, 'chat_id') else 
                       message.peer_id.user_id if hasattr(message.peer_id, 'user_id') else 
                       message.peer_id.channel_id if hasattr(message.peer_id, 'channel_id') else 0,
                message_id=message.id,
                raw_text=getattr(message, 'message', None),
                media_info=await self._extract_media_info(message),
                forwarded_info=await self._extract_forward_info(message),
                reply_info=await self._extract_reply_info(message)
            )
            
            # Process message asynchronously
            task = asyncio.create_task(self._process_message_request(processing_request))
            self._processing_tasks.add(task)
            
            # Clean up completed tasks
            task.add_done_callback(self._processing_tasks.discard)
            
            logger.debug(f"Handling new message {message.id} from chat {processing_request.chat_id}")
            
        except Exception as e:
            logger.error(f"Error handling new message: {str(e)}", exc_info=True)
            self.metrics.error_count += 1
    
    async def _handle_message_edited(self, event) -> None:
        """Handle message edited event"""
        
        try:
            # Call custom event handlers
            for handler in self.event_handlers.get("message_edited", []):
                await handler(event)
                
        except Exception as e:
            logger.error(f"Error handling message edited: {str(e)}")
    
    async def _handle_message_deleted(self, event) -> None:
        """Handle message deleted event"""
        
        try:
            # Call custom event handlers
            for handler in self.event_handlers.get("message_deleted", []):
                await handler(event)
                
        except Exception as e:
            logger.error(f"Error handling message deleted: {str(e)}")
    
    async def _handle_chat_action(self, event) -> None:
        """Handle chat action event (user joined, left, etc.)"""
        
        try:
            # Call custom event handlers
            for handler in self.event_handlers.get("chat_action", []):
                await handler(event)
                
        except Exception as e:
            logger.error(f"Error handling chat action: {str(e)}")
    
    async def _process_message_request(self, request: ProcessingRequest) -> None:
        """Process message request through the pipeline"""
        
        try:
            # Process through unified processor
            result = await self.processor.process_message(request)
            
            if result.success and result.responses:
                # Send responses
                await self.send_messages_batch(
                    result.responses,
                    priority=MessagePriority.NORMAL
                )
                
                logger.debug(
                    f"Processed message {request.message_id} successfully, "
                    f"sent {len(result.responses)} responses"
                )
            else:
                logger.warning(
                    f"Message processing failed: {result.error if result.error else 'Unknown error'}"
                )
            
            # Call custom message handlers
            for handler in self.message_handlers:
                try:
                    await handler(request, result)
                except Exception as e:
                    logger.error(f"Custom message handler error: {str(e)}")
            
        except Exception as e:
            logger.error(f"Error processing message request: {str(e)}", exc_info=True)
    
    async def _extract_media_info(self, message: Message) -> Optional[Dict[str, Any]]:
        """Extract media information from message"""
        
        if not hasattr(message, 'media') or not message.media:
            return None
        
        media_info = {
            "type": type(message.media).__name__,
            "timestamp": time.time()
        }
        
        # Add media-specific information
        if hasattr(message.media, 'photo'):
            media_info.update({
                "media_type": "photo",
                "has_caption": bool(getattr(message, 'message', None))
            })
        elif hasattr(message.media, 'document'):
            doc = message.media.document
            media_info.update({
                "media_type": "document",
                "file_size": getattr(doc, 'size', 0),
                "mime_type": getattr(doc, 'mime_type', 'unknown'),
                "file_name": None
            })
            
            # Extract file name from attributes
            if hasattr(doc, 'attributes'):
                for attr in doc.attributes:
                    if hasattr(attr, 'file_name'):
                        media_info["file_name"] = attr.file_name
                        break
        
        return media_info
    
    async def _extract_forward_info(self, message: Message) -> Optional[Dict[str, Any]]:
        """Extract forward information from message"""
        
        if not hasattr(message, 'fwd_from') or not message.fwd_from:
            return None
        
        fwd_info = {
            "forwarded": True,
            "timestamp": time.time()
        }
        
        if hasattr(message.fwd_from, 'date'):
            fwd_info["original_date"] = message.fwd_from.date.timestamp()
        
        if hasattr(message.fwd_from, 'from_id'):
            fwd_info["from_id"] = message.fwd_from.from_id
        
        return fwd_info
    
    async def _extract_reply_info(self, message: Message) -> Optional[Dict[str, Any]]:
        """Extract reply information from message"""
        
        if not hasattr(message, 'reply_to') or not message.reply_to:
            return None
        
        return {
            "reply_to_message_id": message.reply_to.reply_to_msg_id,
            "timestamp": time.time()
        }
    
    async def _start_queue_processors(self) -> None:
        """Start message queue processors for each priority"""
        
        for priority in MessagePriority:
            task = asyncio.create_task(self._process_message_queue(priority))
            self.queue_processors[priority] = task
            
        logger.info("Message queue processors started")
    
    async def _stop_queue_processors(self) -> None:
        """Stop message queue processors"""
        
        for priority, task in self.queue_processors.items():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        self.queue_processors.clear()
        logger.info("Message queue processors stopped")
    
    async def _process_message_queue(self, priority: MessagePriority) -> None:
        """Process messages from a specific priority queue"""
        
        queue = self.outbound_queue[priority]
        
        while not self._shutdown_event.is_set():
            try:
                if not queue:
                    await asyncio.sleep(0.1)
                    continue
                
                # Get next message
                message = queue.popleft()
                
                # Check rate limiting
                if not await self._check_rate_limit():
                    # Rate limited, put message back
                    queue.appendleft(message)
                    await asyncio.sleep(1.0 / self.rate_limit)
                    continue
                
                # Send message
                success = await self._send_message_internal(message)
                
                if not success and message.retry_count < message.max_retries:
                    # Retry failed message
                    message.retry_count += 1
                    queue.append(message)
                    
                    logger.debug(
                        f"Retrying message to chat {message.chat_id} "
                        f"(attempt {message.retry_count}/{message.max_retries})"
                    )
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Queue processor error for {priority.name}: {str(e)}")
                await asyncio.sleep(1)
    
    async def _send_message_internal(self, message: OutboundMessage) -> bool:
        """Send a single message via Telethon"""
        
        try:
            # Check if we're connected
            if not self.client.is_connected():
                if self.auto_reconnect:
                    await self._attempt_reconnection()
                else:
                    return False
            
            # Send the message
            sent_message = await self.client.send_message(
                entity=message.chat_id,
                message=message.text,
                parse_mode=message.parse_mode,
                reply_to=message.reply_to_message_id,
                link_preview=not message.disable_web_page_preview,
                silent=message.disable_notification
            )
            
            # Handle media attachments
            if message.media_attachments:
                for attachment in message.media_attachments:
                    await self._send_media_attachment(message.chat_id, attachment)
            
            self.metrics.message_send_count += 1
            self._record_message_timestamp()
            
            logger.debug(f"Sent message to chat {message.chat_id}: {message.text[:50]}...")
            return True
            
        except FloodWaitError as e:
            logger.warning(f"Flood wait error: {e.seconds}s")
            self.metrics.flood_wait_count += 1
            
            if self.enable_flood_protection:
                await asyncio.sleep(e.seconds)
                return False  # Will retry
            else:
                return False
                
        except (TelegramConnectionError, AuthKeyError) as e:
            logger.error(f"Connection error: {str(e)}")
            self.status = ClientStatus.ERROR
            
            if self.auto_reconnect:
                await self._attempt_reconnection()
            
            return False
            
        except Exception as e:
            logger.error(f"Failed to send message: {str(e)}", exc_info=True)
            self.metrics.error_count += 1
            return False
    
    async def _send_media_attachment(self, chat_id: int, attachment) -> None:
        """Send media attachment"""
        
        try:
            # This would implement media sending based on attachment type
            # For now, just log that we would send media
            logger.debug(f"Would send media attachment to chat {chat_id}: {attachment.media_type}")
            
        except Exception as e:
            logger.error(f"Failed to send media attachment: {str(e)}")
    
    async def _check_rate_limit(self) -> bool:
        """Check if we can send a message without hitting rate limits"""
        
        current_time = time.time()
        
        # Remove old timestamps
        while self.message_timestamps and current_time - self.message_timestamps[0] > 1.0:
            self.message_timestamps.popleft()
        
        # Check if we're under the rate limit
        return len(self.message_timestamps) < self.rate_limit
    
    def _record_message_timestamp(self) -> None:
        """Record timestamp of sent message for rate limiting"""
        
        self.message_timestamps.append(time.time())
    
    async def _attempt_reconnection(self) -> bool:
        """Attempt to reconnect to Telegram"""
        
        if self.reconnect_attempts >= self.max_reconnect_attempts:
            logger.error(f"Max reconnection attempts ({self.max_reconnect_attempts}) reached")
            return False
        
        self.reconnect_attempts += 1
        self.metrics.reconnection_count += 1
        
        logger.info(f"Attempting reconnection {self.reconnect_attempts}/{self.max_reconnect_attempts}")
        
        try:
            await asyncio.sleep(self.reconnect_delay * self.reconnect_attempts)
            
            # Disconnect first
            if self.client.is_connected():
                await self.client.disconnect()
            
            # Reconnect
            success = await self.connect()
            
            if success:
                logger.info("Reconnection successful")
                return True
            else:
                logger.warning("Reconnection failed")
                return False
                
        except Exception as e:
            logger.error(f"Reconnection attempt failed: {str(e)}")
            return False
    
    async def get_status(self) -> Dict[str, Any]:
        """Get client status and metrics"""
        
        uptime = time.time() - self.start_time if self.start_time else 0
        
        queue_sizes = {
            priority.name.lower(): len(queue)
            for priority, queue in self.outbound_queue.items()
        }
        
        return {
            "status": self.status.value,
            "connected": self.client.is_connected() if self.client else False,
            "uptime_seconds": uptime,
            "reconnect_attempts": self.reconnect_attempts,
            "message_send_count": self.metrics.message_send_count,
            "message_receive_count": self.metrics.message_receive_count,
            "error_count": self.metrics.error_count,
            "flood_wait_count": self.metrics.flood_wait_count,
            "queue_sizes": queue_sizes,
            "total_queued": sum(queue_sizes.values()),
            "processing_tasks": len(self._processing_tasks),
            "rate_limit_per_second": self.rate_limit,
            "current_rate": len(self.message_timestamps),
            "auto_reconnect": self.auto_reconnect
        }
    
    async def get_chat_info(self, chat_id: int) -> Optional[Dict[str, Any]]:
        """Get information about a chat"""
        
        try:
            entity = await self.client.get_entity(chat_id)
            
            chat_info = {
                "id": entity.id,
                "type": type(entity).__name__,
                "title": getattr(entity, 'title', None),
                "username": getattr(entity, 'username', None),
                "first_name": getattr(entity, 'first_name', None),
                "last_name": getattr(entity, 'last_name', None),
                "participant_count": None
            }
            
            # Get participant count for groups/channels
            if isinstance(entity, (Chat, Channel)):
                try:
                    full_chat = await self.client.get_entity(entity)
                    if hasattr(full_chat, 'participants_count'):
                        chat_info["participant_count"] = full_chat.participants_count
                except Exception:
                    pass
            
            return chat_info
            
        except Exception as e:
            logger.error(f"Failed to get chat info for {chat_id}: {str(e)}")
            return None
    
    async def get_message_history(
        self,
        chat_id: int,
        limit: int = 10,
        offset_date: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """Get message history from a chat"""
        
        try:
            messages = []
            
            async for message in self.client.iter_messages(
                entity=chat_id,
                limit=limit,
                offset_date=offset_date
            ):
                msg_dict = {
                    "id": message.id,
                    "date": message.date.timestamp() if message.date else None,
                    "text": getattr(message, 'message', None),
                    "sender_id": getattr(message.sender, 'id', None) if message.sender else None,
                    "reply_to": message.reply_to.reply_to_msg_id if message.reply_to else None,
                    "forwarded": bool(message.fwd_from),
                    "has_media": bool(message.media)
                }
                messages.append(msg_dict)
            
            return messages
            
        except Exception as e:
            logger.error(f"Failed to get message history for {chat_id}: {str(e)}")
            return []