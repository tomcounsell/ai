"""Context Builder Component

This module builds comprehensive message context including message history,
user profiles, workspace information, and conversation state for optimal
agent response generation.
"""

import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from enum import Enum

from pydantic import BaseModel, Field
from telethon.tl.types import Message, User, Chat, Channel

from .security_gate import SecurityResult
from ...agents.valor.context import ValorContext, MessageEntry, UserPreferences
from ...agents.context_manager import ContextWindowManager


logger = logging.getLogger(__name__)


class ConversationMode(Enum):
    """Conversation modes for different interaction types"""
    CASUAL = "casual"
    PROFESSIONAL = "professional"
    TECHNICAL = "technical"
    CREATIVE = "creative"
    ANALYTICAL = "analytical"


class WorkspaceType(Enum):
    """Workspace types for context organization"""
    DEVELOPMENT = "development"
    RESEARCH = "research"
    CREATIVE = "creative"
    PERSONAL = "personal"
    TEAM = "team"


@dataclass
class ConversationState:
    """Current state of a conversation"""
    chat_id: int
    mode: ConversationMode = ConversationMode.CASUAL
    active_topic: Optional[str] = None
    context_summary: str = ""
    last_activity: float = field(default_factory=time.time)
    message_count: int = 0
    participants: Set[int] = field(default_factory=set)
    workspace_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class UserProfile:
    """Enhanced user profile with preferences and history"""
    user_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    preferred_language: str = "en"
    conversation_mode: ConversationMode = ConversationMode.CASUAL
    expertise_areas: List[str] = field(default_factory=list)
    interests: List[str] = field(default_factory=list)
    interaction_count: int = 0
    first_interaction: float = field(default_factory=time.time)
    last_interaction: float = field(default_factory=time.time)
    avg_response_time: float = 60.0  # seconds
    preferred_response_length: str = "medium"  # short, medium, long
    timezone: Optional[str] = None
    custom_preferences: Dict[str, Any] = field(default_factory=dict)


class MessageContext(BaseModel):
    """Comprehensive context for message processing"""
    
    # Core message information
    message_id: int = Field(..., description="Telegram message ID")
    chat_id: int = Field(..., description="Chat identifier")
    user_id: Optional[int] = Field(None, description="User identifier")
    timestamp: float = Field(..., description="Message timestamp")
    
    # Message content
    text_content: Optional[str] = Field(None, description="Message text")
    media_content: Optional[Dict[str, Any]] = Field(None, description="Media information")
    reply_to_message: Optional[Dict[str, Any]] = Field(None, description="Replied message")
    forward_info: Optional[Dict[str, Any]] = Field(None, description="Forward information")
    
    # Context information
    conversation_history: List[MessageEntry] = Field(default_factory=list)
    conversation_state: Optional[ConversationState] = None
    user_profile: Optional[UserProfile] = None
    workspace_context: Optional[Dict[str, Any]] = Field(None, description="Workspace information")
    
    # Security and validation
    security_result: Optional[SecurityResult] = None
    trust_score: float = Field(default=0.5, ge=0.0, le=1.0)
    
    # Processing metadata
    context_tokens: int = Field(default=0, description="Estimated context tokens")
    compression_applied: bool = Field(default=False, description="Whether context was compressed")
    processing_hints: Dict[str, Any] = Field(default_factory=dict)
    
    # Related contexts
    related_conversations: List[int] = Field(default_factory=list)
    cross_references: Dict[str, Any] = Field(default_factory=dict)


class ContextBuilder:
    """
    Advanced context builder that creates comprehensive message context
    from conversation history, user profiles, and workspace information.
    """
    
    def __init__(
        self,
        context_manager: Optional[ContextWindowManager] = None,
        max_history_messages: int = 50,
        max_context_age_hours: int = 168,  # 1 week
        enable_cross_chat_context: bool = False,
        enable_user_profiling: bool = True,
        enable_workspace_context: bool = True,
        context_compression_threshold: int = 80000,  # tokens
        default_conversation_mode: ConversationMode = ConversationMode.CASUAL
    ):
        """
        Initialize the context builder.
        
        Args:
            context_manager: Context window manager for token handling
            max_history_messages: Maximum messages to include in history
            max_context_age_hours: Maximum age of context to include (hours)
            enable_cross_chat_context: Enable context from related chats
            enable_user_profiling: Enable user profile building
            enable_workspace_context: Enable workspace context loading
            context_compression_threshold: Token threshold for compression
            default_conversation_mode: Default conversation mode
        """
        self.context_manager = context_manager or ContextWindowManager()
        self.max_history_messages = max_history_messages
        self.max_context_age_hours = max_context_age_hours
        self.enable_cross_chat_context = enable_cross_chat_context
        self.enable_user_profiling = enable_user_profiling
        self.enable_workspace_context = enable_workspace_context
        self.context_compression_threshold = context_compression_threshold
        self.default_conversation_mode = default_conversation_mode
        
        # Context storage
        self.conversation_states: Dict[int, ConversationState] = {}
        self.user_profiles: Dict[int, UserProfile] = {}
        self.message_cache: Dict[Tuple[int, int], MessageEntry] = {}  # (chat_id, msg_id) -> MessageEntry
        self.conversation_histories: Dict[int, deque] = defaultdict(lambda: deque(maxlen=1000))
        
        # Workspace contexts
        self.workspace_contexts: Dict[str, Dict[str, Any]] = {}
        self.chat_workspaces: Dict[int, str] = {}  # chat_id -> workspace_id
        
        # Performance tracking
        self.context_builds = 0
        self.compression_events = 0
        self.cache_hits = 0
        self.cache_misses = 0
        
        logger.info(
            f"ContextBuilder initialized with max_history={max_history_messages}, "
            f"compression_threshold={context_compression_threshold}, "
            f"profiling={enable_user_profiling}"
        )
    
    async def build_context(
        self,
        chat_id: int,
        user_id: Optional[int],
        message: Message,
        security_context: Optional[SecurityResult] = None,
        workspace_id: Optional[str] = None
    ) -> MessageContext:
        """
        Build comprehensive context for a message.
        
        Args:
            chat_id: Chat identifier
            user_id: User identifier  
            message: Telegram message object
            security_context: Security validation result
            workspace_id: Optional workspace identifier
            
        Returns:
            MessageContext with full conversation context
        """
        start_time = time.perf_counter()
        self.context_builds += 1
        
        try:
            current_time = time.time()
            message_id = message.id
            
            # Extract message content
            text_content = getattr(message, 'message', None) or getattr(message, 'text', None)
            media_content = await self._extract_media_info(message)
            reply_info = await self._extract_reply_info(message)
            forward_info = await self._extract_forward_info(message)
            
            # Get or create conversation state
            conversation_state = await self._get_conversation_state(
                chat_id, user_id, current_time
            )
            
            # Build user profile
            user_profile = None
            if self.enable_user_profiling and user_id:
                user_profile = await self._build_user_profile(
                    user_id, message, current_time
                )
            
            # Load conversation history
            conversation_history = await self._load_conversation_history(
                chat_id, message_id, current_time
            )
            
            # Load workspace context
            workspace_context = None
            if self.enable_workspace_context:
                workspace_context = await self._load_workspace_context(
                    chat_id, workspace_id or conversation_state.workspace_id
                )
            
            # Create message entry for current message
            current_message_entry = MessageEntry(
                role="user",
                content=text_content or "[Media message]",
                timestamp=current_time,
                message_id=message_id,
                user_id=user_id,
                metadata={
                    "chat_id": chat_id,
                    "has_media": media_content is not None,
                    "is_reply": reply_info is not None,
                    "is_forward": forward_info is not None
                }
            )
            
            # Add to history and cache
            self.conversation_histories[chat_id].append(current_message_entry)
            self.message_cache[(chat_id, message_id)] = current_message_entry
            
            # Build context
            context = MessageContext(
                message_id=message_id,
                chat_id=chat_id,
                user_id=user_id,
                timestamp=current_time,
                text_content=text_content,
                media_content=media_content,
                reply_to_message=reply_info,
                forward_info=forward_info,
                conversation_history=conversation_history,
                conversation_state=conversation_state,
                user_profile=user_profile,
                workspace_context=workspace_context,
                security_result=security_context,
                trust_score=security_context.user_trust_score if security_context else 0.5
            )
            
            # Estimate token count and compress if needed
            context = await self._optimize_context(context)
            
            # Update conversation state
            await self._update_conversation_state(
                conversation_state, current_message_entry, context
            )
            
            # Add processing hints
            context.processing_hints = await self._generate_processing_hints(context)
            
            # Find related conversations if enabled
            if self.enable_cross_chat_context and user_id:
                context.related_conversations = await self._find_related_conversations(
                    user_id, chat_id, text_content
                )
            
            build_time = (time.perf_counter() - start_time) * 1000
            
            logger.debug(
                f"Context built for message {message_id} in {build_time:.1f}ms, "
                f"history_size={len(conversation_history)}, "
                f"tokens={context.context_tokens}"
            )
            
            return context
            
        except Exception as e:
            logger.error(
                f"Error building context for message {message.id}: {str(e)}", 
                exc_info=True
            )
            # Return minimal context on error
            return MessageContext(
                message_id=message.id,
                chat_id=chat_id,
                user_id=user_id,
                timestamp=time.time(),
                text_content=getattr(message, 'message', None),
                security_result=security_context
            )
    
    async def _get_conversation_state(
        self,
        chat_id: int,
        user_id: Optional[int],
        current_time: float
    ) -> ConversationState:
        """Get or create conversation state"""
        if chat_id not in self.conversation_states:
            self.conversation_states[chat_id] = ConversationState(
                chat_id=chat_id,
                mode=self.default_conversation_mode,
                last_activity=current_time
            )
        
        state = self.conversation_states[chat_id]
        state.last_activity = current_time
        state.message_count += 1
        
        if user_id:
            state.participants.add(user_id)
        
        return state
    
    async def _build_user_profile(
        self,
        user_id: int,
        message: Message,
        current_time: float
    ) -> UserProfile:
        """Build or update user profile"""
        if user_id not in self.user_profiles:
            # Extract user info from message
            user_info = getattr(message, 'from_id', None)
            username = None
            first_name = None
            last_name = None
            
            # Try to get user details if available
            if hasattr(message, 'sender') and message.sender:
                username = getattr(message.sender, 'username', None)
                first_name = getattr(message.sender, 'first_name', None)
                last_name = getattr(message.sender, 'last_name', None)
            
            self.user_profiles[user_id] = UserProfile(
                user_id=user_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                first_interaction=current_time
            )
        
        profile = self.user_profiles[user_id]
        profile.interaction_count += 1
        profile.last_interaction = current_time
        
        # Analyze message for profile insights
        message_text = getattr(message, 'message', None) or getattr(message, 'text', None)
        if message_text:
            await self._analyze_message_for_profile(profile, message_text)
        
        return profile
    
    async def _analyze_message_for_profile(
        self,
        profile: UserProfile,
        message_text: str
    ) -> None:
        """Analyze message content to update user profile"""
        text_lower = message_text.lower()
        
        # Detect technical expertise
        technical_keywords = [
            'python', 'javascript', 'react', 'django', 'api', 'database',
            'sql', 'git', 'docker', 'kubernetes', 'aws', 'machine learning',
            'ai', 'algorithm', 'framework', 'library', 'deployment'
        ]
        
        for keyword in technical_keywords:
            if keyword in text_lower and keyword not in profile.expertise_areas:
                profile.expertise_areas.append(keyword)
        
        # Detect interests
        interest_patterns = {
            'gaming': ['game', 'gaming', 'steam', 'playstation', 'xbox'],
            'music': ['music', 'song', 'album', 'artist', 'concert'],
            'movies': ['movie', 'film', 'cinema', 'netflix', 'series'],
            'sports': ['football', 'soccer', 'basketball', 'tennis', 'sports'],
            'cooking': ['recipe', 'cooking', 'food', 'restaurant', 'chef'],
            'travel': ['travel', 'vacation', 'trip', 'flight', 'hotel']
        }
        
        for interest, keywords in interest_patterns.items():
            if any(keyword in text_lower for keyword in keywords):
                if interest not in profile.interests:
                    profile.interests.append(interest)
        
        # Detect conversation mode preference
        if len(message_text) > 200 and any(word in text_lower for word in ['technical', 'implementation', 'architecture']):
            profile.conversation_mode = ConversationMode.TECHNICAL
        elif any(word in text_lower for word in ['creative', 'idea', 'design', 'art']):
            profile.conversation_mode = ConversationMode.CREATIVE
        elif any(word in text_lower for word in ['analyze', 'data', 'statistics', 'research']):
            profile.conversation_mode = ConversationMode.ANALYTICAL
    
    async def _load_conversation_history(
        self,
        chat_id: int,
        current_message_id: int,
        current_time: float
    ) -> List[MessageEntry]:
        """Load relevant conversation history"""
        history = list(self.conversation_histories[chat_id])
        
        # Filter by age
        max_age = current_time - (self.max_context_age_hours * 3600)
        history = [msg for msg in history if msg.timestamp >= max_age]
        
        # Limit count
        if len(history) > self.max_history_messages:
            # Keep most recent messages and some important ones
            recent_messages = history[-self.max_history_messages//2:]
            important_messages = [
                msg for msg in history[:-self.max_history_messages//2]
                if msg.importance_score > 7.0
            ][:self.max_history_messages//2]
            history = important_messages + recent_messages
        
        return history
    
    async def _load_workspace_context(
        self,
        chat_id: int,
        workspace_id: Optional[str]
    ) -> Optional[Dict[str, Any]]:
        """Load workspace context if available"""
        if not workspace_id:
            # Try to find workspace for this chat
            workspace_id = self.chat_workspaces.get(chat_id)
        
        if workspace_id and workspace_id in self.workspace_contexts:
            return self.workspace_contexts[workspace_id]
        
        # Try to detect workspace from chat context
        conversation_state = self.conversation_states.get(chat_id)
        if conversation_state and conversation_state.active_topic:
            # Create implicit workspace
            workspace_context = {
                "type": "implicit",
                "topic": conversation_state.active_topic,
                "participants": list(conversation_state.participants),
                "created_at": time.time(),
                "activity_level": conversation_state.message_count
            }
            return workspace_context
        
        return None
    
    async def _optimize_context(self, context: MessageContext) -> MessageContext:
        """Optimize context size and apply compression if needed"""
        # Estimate token count
        context.context_tokens = await self._estimate_context_tokens(context)
        
        # Apply compression if over threshold
        if context.context_tokens > self.context_compression_threshold:
            context = await self._compress_context(context)
            self.compression_events += 1
        
        return context
    
    async def _estimate_context_tokens(self, context: MessageContext) -> int:
        """Estimate token count for context"""
        token_count = 0
        
        # Text content
        if context.text_content:
            token_count += len(context.text_content.split()) * 1.3  # Rough estimation
        
        # Conversation history
        for msg in context.conversation_history:
            token_count += len(msg.content.split()) * 1.3
        
        # User profile
        if context.user_profile:
            token_count += 100  # Estimated profile size
        
        # Workspace context
        if context.workspace_context:
            token_count += 200  # Estimated workspace size
        
        return int(token_count)
    
    async def _compress_context(self, context: MessageContext) -> MessageContext:
        """Compress context while preserving important information"""
        # Compress conversation history
        if len(context.conversation_history) > 20:
            # Keep recent messages and high-importance messages
            recent_messages = context.conversation_history[-10:]
            important_messages = [
                msg for msg in context.conversation_history[:-10]
                if msg.importance_score > 8.0
            ][:10]
            
            context.conversation_history = important_messages + recent_messages
            context.compression_applied = True
        
        # Summarize older context
        if context.conversation_state:
            older_messages = [
                msg for msg in context.conversation_history[:-20]
                if msg.importance_score < 7.0
            ]
            if older_messages:
                summary = await self._create_conversation_summary(older_messages)
                context.conversation_state.context_summary = summary
        
        return context
    
    async def _create_conversation_summary(
        self, 
        messages: List[MessageEntry]
    ) -> str:
        """Create a summary of conversation messages"""
        # Simple extractive summary for now
        important_messages = [
            msg.content for msg in messages 
            if msg.importance_score > 6.0
        ][:5]
        
        if important_messages:
            return "Previous conversation topics: " + "; ".join(important_messages[:100] for content in important_messages)
        else:
            return f"Previous conversation with {len(messages)} messages"
    
    async def _extract_media_info(self, message: Message) -> Optional[Dict[str, Any]]:
        """Extract media information from message"""
        if not hasattr(message, 'media') or not message.media:
            return None
        
        media_info = {
            "type": type(message.media).__name__,
            "timestamp": time.time()
        }
        
        # Add type-specific information
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
                "mime_type": getattr(doc, 'mime_type', 'unknown')
            })
        
        return media_info
    
    async def _extract_reply_info(self, message: Message) -> Optional[Dict[str, Any]]:
        """Extract reply information from message"""
        if not hasattr(message, 'reply_to') or not message.reply_to:
            return None
        
        reply_to_msg_id = message.reply_to.reply_to_msg_id
        
        return {
            "reply_to_message_id": reply_to_msg_id,
            "timestamp": time.time()
        }
    
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
        
        return fwd_info
    
    async def _update_conversation_state(
        self,
        state: ConversationState,
        message_entry: MessageEntry,
        context: MessageContext
    ) -> None:
        """Update conversation state based on new message"""
        # Extract topic from message content
        if message_entry.content and len(message_entry.content) > 20:
            # Simple topic extraction - could be enhanced with NLP
            words = message_entry.content.lower().split()
            technical_words = [w for w in words if len(w) > 5 and w.isalpha()]
            if technical_words:
                state.active_topic = technical_words[0]
        
        # Update conversation mode based on user profile
        if context.user_profile:
            state.mode = context.user_profile.conversation_mode
        
        # Update metadata
        state.metadata.update({
            "last_message_length": len(message_entry.content),
            "has_media": context.media_content is not None,
            "context_tokens": context.context_tokens
        })
    
    async def _generate_processing_hints(
        self, 
        context: MessageContext
    ) -> Dict[str, Any]:
        """Generate processing hints for the agent"""
        hints = {}
        
        # Response length preference
        if context.user_profile:
            hints["preferred_response_length"] = context.user_profile.preferred_response_length
            hints["user_expertise"] = context.user_profile.expertise_areas
            hints["user_interests"] = context.user_profile.interests
        
        # Conversation context hints
        if context.conversation_state:
            hints["conversation_mode"] = context.conversation_state.mode.value
            hints["active_topic"] = context.conversation_state.active_topic
            hints["participant_count"] = len(context.conversation_state.participants)
        
        # Message context hints
        hints["has_media"] = context.media_content is not None
        hints["is_reply"] = context.reply_to_message is not None
        hints["is_forward"] = context.forward_info is not None
        hints["message_length"] = len(context.text_content) if context.text_content else 0
        
        # Trust and security hints
        hints["trust_level"] = "high" if context.trust_score > 0.8 else "medium" if context.trust_score > 0.4 else "low"
        
        return hints
    
    async def _find_related_conversations(
        self,
        user_id: int,
        current_chat_id: int,
        message_text: Optional[str]
    ) -> List[int]:
        """Find related conversations for cross-context reference"""
        related_chats = []
        
        # Find chats where this user is active
        for chat_id, state in self.conversation_states.items():
            if chat_id != current_chat_id and user_id in state.participants:
                # Check topic similarity
                if (message_text and state.active_topic and 
                    state.active_topic.lower() in message_text.lower()):
                    related_chats.append(chat_id)
        
        return related_chats[:3]  # Limit to 3 related conversations
    
    async def create_workspace(
        self,
        workspace_id: str,
        workspace_type: WorkspaceType,
        description: str,
        participants: List[int],
        settings: Optional[Dict[str, Any]] = None
    ) -> None:
        """Create a new workspace context"""
        self.workspace_contexts[workspace_id] = {
            "id": workspace_id,
            "type": workspace_type.value,
            "description": description,
            "participants": participants,
            "created_at": time.time(),
            "settings": settings or {},
            "activity_count": 0,
            "last_activity": time.time()
        }
        logger.info(f"Created workspace {workspace_id} of type {workspace_type.value}")
    
    async def assign_chat_to_workspace(
        self,
        chat_id: int,
        workspace_id: str
    ) -> None:
        """Assign a chat to a workspace"""
        if workspace_id in self.workspace_contexts:
            self.chat_workspaces[chat_id] = workspace_id
            self.workspace_contexts[workspace_id]["activity_count"] += 1
            logger.info(f"Assigned chat {chat_id} to workspace {workspace_id}")
        else:
            logger.warning(f"Workspace {workspace_id} not found")
    
    async def get_status(self) -> Dict[str, Any]:
        """Get context builder status and statistics"""
        total_conversations = len(self.conversation_states)
        total_users = len(self.user_profiles)
        total_workspaces = len(self.workspace_contexts)
        
        cache_hit_rate = (
            self.cache_hits / (self.cache_hits + self.cache_misses)
            if (self.cache_hits + self.cache_misses) > 0 else 0.0
        )
        
        return {
            "context_builds": self.context_builds,
            "compression_events": self.compression_events,
            "active_conversations": total_conversations,
            "total_users": total_users,
            "total_workspaces": total_workspaces,
            "cache_hit_rate": cache_hit_rate,
            "cache_size": len(self.message_cache),
            "avg_history_size": (
                sum(len(hist) for hist in self.conversation_histories.values()) / 
                len(self.conversation_histories)
                if self.conversation_histories else 0
            )
        }
    
    async def shutdown(self) -> None:
        """Gracefully shutdown the context builder"""
        logger.info("Shutting down context builder...")
        
        # Save important state if needed
        # Clear caches
        self.message_cache.clear()
        self.conversation_histories.clear()
        
        logger.info("Context builder shutdown complete")