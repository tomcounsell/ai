"""Type Router Component

This module implements intelligent message type detection and routing for
multi-modal content processing, directing messages to appropriate handlers
based on content analysis and context.
"""

import asyncio
import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from enum import Enum

from pydantic import BaseModel, Field
from telethon.tl.types import Message, MessageMediaPhoto, MessageMediaDocument, MessageMediaContact

from .context_builder import MessageContext


logger = logging.getLogger(__name__)


class MessageType(Enum):
    """Comprehensive message type classification"""
    
    # Text-based messages
    TEXT_CASUAL = "text_casual"
    TEXT_QUESTION = "text_question"  
    TEXT_COMMAND = "text_command"
    TEXT_TECHNICAL = "text_technical"
    TEXT_CREATIVE = "text_creative"
    
    # Media messages
    IMAGE_PHOTO = "image_photo"
    IMAGE_DOCUMENT = "image_document"
    IMAGE_SCREENSHOT = "image_screenshot"
    IMAGE_DIAGRAM = "image_diagram"
    
    # Document messages
    DOCUMENT_CODE = "document_code"
    DOCUMENT_TEXT = "document_text"
    DOCUMENT_PDF = "document_pdf"
    DOCUMENT_ARCHIVE = "document_archive"
    DOCUMENT_OTHER = "document_other"
    
    # Audio/Video
    AUDIO_VOICE = "audio_voice"
    AUDIO_MUSIC = "audio_music"
    VIDEO_MESSAGE = "video_message"
    VIDEO_FILE = "video_file"
    
    # Interactive messages
    CONTACT_SHARE = "contact_share"
    LOCATION_SHARE = "location_share"
    POLL_MESSAGE = "poll_message"
    
    # Special messages
    FORWARDED_MESSAGE = "forwarded_message"
    REPLY_MESSAGE = "reply_message"
    EDIT_MESSAGE = "edit_message"
    DELETE_MESSAGE = "delete_message"
    
    # System messages
    SYSTEM_JOIN = "system_join"
    SYSTEM_LEAVE = "system_leave"
    SYSTEM_UPDATE = "system_update"
    
    # Unknown/Error
    UNKNOWN = "unknown"


class ProcessingPriority(Enum):
    """Processing priority levels"""
    IMMEDIATE = "immediate"  # Commands, urgent questions
    HIGH = "high"           # Technical questions, media analysis
    NORMAL = "normal"       # Regular conversation
    LOW = "low"            # Casual messages, system updates
    BACKGROUND = "background"  # Analytics, logging


class RoutingStrategy(Enum):
    """Routing strategy for different message types"""
    DIRECT = "direct"           # Route directly to primary handler
    PARALLEL = "parallel"       # Process by multiple handlers
    SEQUENTIAL = "sequential"   # Process in sequence
    CONDITIONAL = "conditional" # Route based on conditions
    BROADCAST = "broadcast"     # Send to all compatible handlers


@dataclass
class TypeSignature:
    """Signature pattern for message type detection"""
    name: str
    patterns: List[str]
    media_types: List[str]
    file_extensions: List[str] 
    keywords: List[str]
    context_hints: List[str]
    confidence_threshold: float = 0.7
    priority: ProcessingPriority = ProcessingPriority.NORMAL
    routing_strategy: RoutingStrategy = RoutingStrategy.DIRECT


class RouteResult(BaseModel):
    """Result of message type routing"""
    
    message_type: MessageType = Field(..., description="Detected message type")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Detection confidence")
    priority: ProcessingPriority = Field(..., description="Processing priority")
    routing_strategy: RoutingStrategy = Field(..., description="Routing strategy")
    
    # Routing information
    primary_handler: str = Field(..., description="Primary handler name")
    secondary_handlers: List[str] = Field(default_factory=list)
    handler_config: Dict[str, Any] = Field(default_factory=dict)
    
    # Analysis results
    content_features: Dict[str, Any] = Field(default_factory=dict)
    media_analysis: Optional[Dict[str, Any]] = None
    language_info: Optional[Dict[str, str]] = None
    
    # Processing hints
    estimated_processing_time: float = Field(default=1.0)
    requires_special_handling: bool = Field(default=False)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TypeRouter:
    """
    Advanced message type router with multi-modal content analysis,
    intelligent classification, and optimized routing decisions.
    """
    
    def __init__(
        self,
        enable_content_analysis: bool = True,
        enable_media_analysis: bool = True,
        enable_language_detection: bool = True,
        confidence_threshold: float = 0.7,
        enable_learning: bool = True,
        max_analysis_time_ms: int = 500
    ):
        """
        Initialize the type router.
        
        Args:
            enable_content_analysis: Enable deep content analysis
            enable_media_analysis: Enable media type analysis
            enable_language_detection: Enable language detection
            confidence_threshold: Minimum confidence for type detection
            enable_learning: Enable adaptive learning from routing feedback
            max_analysis_time_ms: Maximum time for analysis (ms)
        """
        self.enable_content_analysis = enable_content_analysis
        self.enable_media_analysis = enable_media_analysis
        self.enable_language_detection = enable_language_detection
        self.confidence_threshold = confidence_threshold
        self.enable_learning = enable_learning
        self.max_analysis_time_ms = max_analysis_time_ms
        
        # Type signatures for classification
        self.type_signatures = self._initialize_type_signatures()
        
        # Handler mappings
        self.handler_mappings = self._initialize_handler_mappings()
        
        # Language detection patterns
        self.language_patterns = self._initialize_language_patterns()
        
        # Learning data
        self.classification_history: List[Dict[str, Any]] = []
        self.feedback_data: Dict[str, List[float]] = defaultdict(list)
        self.handler_performance: Dict[str, Dict[str, float]] = defaultdict(dict)
        
        # Statistics
        self.total_classifications = 0
        self.classification_accuracy = 0.0
        self.avg_confidence = 0.0
        self.processing_times: List[float] = []
        
        logger.info(
            f"TypeRouter initialized with content_analysis={enable_content_analysis}, "
            f"media_analysis={enable_media_analysis}, "
            f"threshold={confidence_threshold}"
        )
    
    async def route_message(
        self,
        message: Message,
        context: MessageContext,
        media_info: Optional[Dict[str, Any]] = None
    ) -> RouteResult:
        """
        Analyze and route a message based on its type and content.
        
        Args:
            message: Telegram message object
            context: Message context from context builder
            media_info: Optional media information
            
        Returns:
            RouteResult with classification and routing information
        """
        start_time = time.perf_counter()
        self.total_classifications += 1
        
        try:
            # Extract basic message information
            text_content = context.text_content or ""
            has_media = media_info is not None or context.media_content is not None
            
            # Perform multi-modal analysis
            analysis_results = await self._perform_multimodal_analysis(
                message, text_content, media_info or context.media_content, context
            )
            
            # Classify message type
            message_type, confidence = await self._classify_message_type(
                text_content, analysis_results, context
            )
            
            # Determine processing priority
            priority = self._determine_priority(message_type, analysis_results, context)
            
            # Select routing strategy
            routing_strategy = self._select_routing_strategy(message_type, analysis_results)
            
            # Map to handlers
            primary_handler, secondary_handlers = await self._map_to_handlers(
                message_type, analysis_results, context
            )
            
            # Generate handler configuration
            handler_config = await self._generate_handler_config(
                message_type, analysis_results, context
            )
            
            # Estimate processing time
            estimated_time = self._estimate_processing_time(message_type, analysis_results)
            
            # Create route result
            route_result = RouteResult(
                message_type=message_type,
                confidence=confidence,
                priority=priority,
                routing_strategy=routing_strategy,
                primary_handler=primary_handler,
                secondary_handlers=secondary_handlers,
                handler_config=handler_config,
                content_features=analysis_results.get("content_features", {}),
                media_analysis=analysis_results.get("media_analysis"),
                language_info=analysis_results.get("language_info"),
                estimated_processing_time=estimated_time,
                requires_special_handling=self._requires_special_handling(message_type, analysis_results),
                metadata={
                    "analysis_time_ms": (time.perf_counter() - start_time) * 1000,
                    "has_media": has_media,
                    "text_length": len(text_content),
                    "context_tokens": context.context_tokens
                }
            )
            
            # Store for learning
            if self.enable_learning:
                self._record_classification(message_type, confidence, analysis_results)
            
            # Update statistics
            self._update_statistics(confidence, time.perf_counter() - start_time)
            
            logger.debug(
                f"Message routed as {message_type.value} with confidence {confidence:.2f}, "
                f"handler: {primary_handler}, priority: {priority.value}"
            )
            
            return route_result
            
        except Exception as e:
            logger.error(f"Error routing message: {str(e)}", exc_info=True)
            
            # Return fallback routing
            return RouteResult(
                message_type=MessageType.UNKNOWN,
                confidence=0.0,
                priority=ProcessingPriority.NORMAL,
                routing_strategy=RoutingStrategy.DIRECT,
                primary_handler="general_handler",
                estimated_processing_time=2.0,
                metadata={"error": str(e)}
            )
    
    async def _perform_multimodal_analysis(
        self,
        message: Message,
        text_content: str,
        media_info: Optional[Dict[str, Any]],
        context: MessageContext
    ) -> Dict[str, Any]:
        """Perform comprehensive multimodal content analysis"""
        analysis = {}
        
        # Text content analysis
        if self.enable_content_analysis and text_content:
            analysis["content_features"] = await self._analyze_text_content(text_content, context)
        
        # Media analysis
        if self.enable_media_analysis and media_info:
            analysis["media_analysis"] = await self._analyze_media_content(message, media_info)
        
        # Language detection
        if self.enable_language_detection and text_content:
            analysis["language_info"] = await self._detect_language(text_content)
        
        # Context analysis
        analysis["context_features"] = await self._analyze_context(context)
        
        return analysis
    
    async def _analyze_text_content(
        self,
        text: str,
        context: MessageContext
    ) -> Dict[str, Any]:
        """Analyze text content for classification features"""
        features = {}
        
        # Basic metrics
        features["length"] = len(text)
        features["word_count"] = len(text.split())
        features["sentence_count"] = len(re.split(r'[.!?]+', text))
        
        # Question detection
        question_patterns = [
            r'\?',
            r'^(what|how|when|where|why|which|who|can|could|would|should|is|are|do|does|did)',
            r'(help|explain|tell me|show me)'
        ]
        features["is_question"] = any(re.search(pattern, text, re.IGNORECASE) for pattern in question_patterns)
        
        # Command detection
        command_patterns = [
            r'^[!/](\w+)',  # /command or !command
            r'^(please|can you|could you)\s+(do|make|create|generate)',
            r'(run|execute|start|stop|restart)'
        ]
        features["is_command"] = any(re.search(pattern, text, re.IGNORECASE) for pattern in command_patterns)
        
        # Technical content detection
        technical_keywords = [
            'code', 'function', 'class', 'method', 'variable', 'algorithm', 'database',
            'api', 'server', 'client', 'framework', 'library', 'deployment', 'bug',
            'error', 'exception', 'debug', 'test', 'unit test', 'integration'
        ]
        features["technical_score"] = sum(1 for keyword in technical_keywords if keyword in text.lower()) / len(technical_keywords)
        
        # Creative content detection
        creative_keywords = [
            'story', 'poem', 'creative', 'imagine', 'design', 'art', 'drawing',
            'painting', 'music', 'song', 'novel', 'character', 'plot', 'scene'
        ]
        features["creative_score"] = sum(1 for keyword in creative_keywords if keyword in text.lower()) / len(creative_keywords)
        
        # Urgency detection
        urgency_patterns = [
            r'(urgent|asap|immediately|now|quickly|fast|help)',
            r'(emergency|critical|important|priority)'
        ]
        features["urgency_score"] = sum(1 for pattern in urgency_patterns if re.search(pattern, text, re.IGNORECASE)) / len(urgency_patterns)
        
        # Sentiment analysis (basic)
        positive_words = ['good', 'great', 'excellent', 'awesome', 'perfect', 'love', 'like', 'happy', 'thanks']
        negative_words = ['bad', 'terrible', 'awful', 'hate', 'dislike', 'angry', 'frustrated', 'problem', 'issue']
        
        text_lower = text.lower()
        positive_count = sum(1 for word in positive_words if word in text_lower)
        negative_count = sum(1 for word in negative_words if word in text_lower)
        
        if positive_count + negative_count > 0:
            features["sentiment_score"] = (positive_count - negative_count) / (positive_count + negative_count)
        else:
            features["sentiment_score"] = 0.0
        
        # Code detection
        code_patterns = [
            r'```[\s\S]*?```',  # Code blocks
            r'`[^`]+`',         # Inline code
            r'(def|function|class|import|from|if|for|while|try|except)\s',
            r'[a-zA-Z_][a-zA-Z0-9_]*\s*\([^)]*\)\s*{',  # Function calls
            r'[a-zA-Z_][a-zA-Z0-9_]*\.[a-zA-Z_][a-zA-Z0-9_]*'  # Object methods
        ]
        features["has_code"] = any(re.search(pattern, text, re.MULTILINE) for pattern in code_patterns)
        
        # URL detection
        features["has_urls"] = bool(re.search(r'http[s]?://[^\s]+', text))
        
        return features
    
    async def _analyze_media_content(
        self,
        message: Message,
        media_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Analyze media content for type classification"""
        analysis = {}
        
        media_type = media_info.get("type", "").lower()
        analysis["media_type"] = media_type
        
        # Photo analysis
        if "photo" in media_type or isinstance(getattr(message, 'media', None), MessageMediaPhoto):
            analysis["is_photo"] = True
            analysis["likely_screenshot"] = False  # Would need image analysis
            analysis["likely_diagram"] = False     # Would need image analysis
        
        # Document analysis
        elif "document" in media_type or isinstance(getattr(message, 'media', None), MessageMediaDocument):
            analysis["is_document"] = True
            
            # File type detection
            mime_type = media_info.get("mime_type", "")
            file_size = media_info.get("file_size", 0)
            
            if "image" in mime_type:
                analysis["document_type"] = "image"
            elif "text" in mime_type or mime_type in ["application/json", "application/xml"]:
                analysis["document_type"] = "text"
            elif "pdf" in mime_type:
                analysis["document_type"] = "pdf"
            elif mime_type in ["application/zip", "application/rar", "application/tar"]:
                analysis["document_type"] = "archive"
            elif "code" in mime_type or mime_type in ["text/x-python", "text/javascript"]:
                analysis["document_type"] = "code"
            else:
                analysis["document_type"] = "other"
            
            analysis["file_size_mb"] = file_size / (1024 * 1024) if file_size else 0
        
        # Audio analysis
        elif "audio" in media_type:
            analysis["is_audio"] = True
            analysis["is_voice_note"] = "voice" in media_info.get("media_type", "").lower()
        
        # Video analysis  
        elif "video" in media_type:
            analysis["is_video"] = True
            analysis["is_video_note"] = "video_note" in media_info.get("media_type", "").lower()
        
        # Contact/Location
        elif isinstance(getattr(message, 'media', None), MessageMediaContact):
            analysis["is_contact"] = True
        
        return analysis
    
    async def _detect_language(self, text: str) -> Dict[str, str]:
        """Simple language detection based on patterns"""
        # Basic language detection using character patterns and common words
        
        # English indicators
        english_patterns = [
            r'\b(the|and|or|but|in|on|at|to|for|of|with|by)\b',
            r'\b(is|are|was|were|have|has|had|will|would|can|could)\b'
        ]
        english_score = sum(len(re.findall(pattern, text, re.IGNORECASE)) for pattern in english_patterns)
        
        # Python code indicators
        python_patterns = [
            r'\b(def|class|import|from|if|elif|else|for|while|try|except|with|as)\b',
            r'\b(print|len|range|list|dict|str|int|float)\b'
        ]
        python_score = sum(len(re.findall(pattern, text)) for pattern in python_patterns)
        
        # JavaScript indicators
        js_patterns = [
            r'\b(function|var|let|const|if|else|for|while|return|class)\b',
            r'\b(console\.log|document\.|window\.)\b'
        ]
        js_score = sum(len(re.findall(pattern, text)) for pattern in js_patterns)
        
        # Determine primary language
        scores = {
            "english": english_score,
            "python": python_score,
            "javascript": js_score
        }
        
        primary_language = max(scores, key=scores.get) if any(scores.values()) else "unknown"
        confidence = max(scores.values()) / len(text.split()) if text.split() else 0.0
        
        return {
            "primary": primary_language,
            "confidence": min(confidence, 1.0),
            "detected_languages": [lang for lang, score in scores.items() if score > 0]
        }
    
    async def _analyze_context(self, context: MessageContext) -> Dict[str, Any]:
        """Analyze message context for routing hints"""
        features = {}
        
        # Reply context
        features["is_reply"] = context.reply_to_message is not None
        features["is_forward"] = context.forward_info is not None
        
        # User context
        if context.user_profile:
            features["user_expertise"] = context.user_profile.expertise_areas
            features["user_mode"] = context.user_profile.conversation_mode.value
            features["user_interaction_count"] = context.user_profile.interaction_count
        
        # Conversation context
        if context.conversation_state:
            features["conversation_mode"] = context.conversation_state.mode.value
            features["active_topic"] = context.conversation_state.active_topic
            features["message_count_in_conversation"] = context.conversation_state.message_count
        
        # Workspace context
        if context.workspace_context:
            features["workspace_type"] = context.workspace_context.get("type")
            features["in_workspace"] = True
        else:
            features["in_workspace"] = False
        
        return features
    
    async def _classify_message_type(
        self,
        text_content: str,
        analysis_results: Dict[str, Any],
        context: MessageContext
    ) -> Tuple[MessageType, float]:
        """Classify message type based on analysis results"""
        
        content_features = analysis_results.get("content_features", {})
        media_analysis = analysis_results.get("media_analysis", {})
        context_features = analysis_results.get("context_features", {})
        
        # Media message classification
        if media_analysis:
            if media_analysis.get("is_photo"):
                if media_analysis.get("likely_screenshot"):
                    return MessageType.IMAGE_SCREENSHOT, 0.9
                elif media_analysis.get("likely_diagram"):
                    return MessageType.IMAGE_DIAGRAM, 0.9
                else:
                    return MessageType.IMAGE_PHOTO, 0.8
            
            elif media_analysis.get("is_document"):
                doc_type = media_analysis.get("document_type", "other")
                if doc_type == "code":
                    return MessageType.DOCUMENT_CODE, 0.9
                elif doc_type == "text":
                    return MessageType.DOCUMENT_TEXT, 0.8
                elif doc_type == "pdf":
                    return MessageType.DOCUMENT_PDF, 0.9
                elif doc_type == "archive":
                    return MessageType.DOCUMENT_ARCHIVE, 0.8
                else:
                    return MessageType.DOCUMENT_OTHER, 0.7
            
            elif media_analysis.get("is_audio"):
                if media_analysis.get("is_voice_note"):
                    return MessageType.AUDIO_VOICE, 0.9
                else:
                    return MessageType.AUDIO_MUSIC, 0.8
            
            elif media_analysis.get("is_video"):
                if media_analysis.get("is_video_note"):
                    return MessageType.VIDEO_MESSAGE, 0.9
                else:
                    return MessageType.VIDEO_FILE, 0.8
            
            elif media_analysis.get("is_contact"):
                return MessageType.CONTACT_SHARE, 0.95
        
        # Text message classification
        if text_content and content_features:
            # Command detection
            if content_features.get("is_command", False):
                return MessageType.TEXT_COMMAND, 0.9
            
            # Question detection
            if content_features.get("is_question", False):
                if content_features.get("technical_score", 0) > 0.3:
                    return MessageType.TEXT_TECHNICAL, 0.8
                else:
                    return MessageType.TEXT_QUESTION, 0.8
            
            # Technical content
            if content_features.get("technical_score", 0) > 0.4 or content_features.get("has_code", False):
                return MessageType.TEXT_TECHNICAL, 0.85
            
            # Creative content
            if content_features.get("creative_score", 0) > 0.3:
                return MessageType.TEXT_CREATIVE, 0.8
            
            # Default to casual text
            return MessageType.TEXT_CASUAL, 0.7
        
        # Special message types
        if context_features.get("is_reply", False):
            return MessageType.REPLY_MESSAGE, 0.8
        
        if context_features.get("is_forward", False):
            return MessageType.FORWARDED_MESSAGE, 0.8
        
        # Fallback
        return MessageType.UNKNOWN, 0.5
    
    def _determine_priority(
        self,
        message_type: MessageType,
        analysis_results: Dict[str, Any],
        context: MessageContext
    ) -> ProcessingPriority:
        """Determine processing priority based on message type and content"""
        
        content_features = analysis_results.get("content_features", {})
        
        # Immediate priority
        if message_type == MessageType.TEXT_COMMAND:
            return ProcessingPriority.IMMEDIATE
        
        if content_features.get("urgency_score", 0) > 0.5:
            return ProcessingPriority.IMMEDIATE
        
        # High priority
        if message_type in [MessageType.TEXT_TECHNICAL, MessageType.TEXT_QUESTION]:
            return ProcessingPriority.HIGH
        
        if message_type in [MessageType.DOCUMENT_CODE, MessageType.IMAGE_SCREENSHOT]:
            return ProcessingPriority.HIGH
        
        # Normal priority
        if message_type in [MessageType.TEXT_CASUAL, MessageType.TEXT_CREATIVE]:
            return ProcessingPriority.NORMAL
        
        if message_type in [MessageType.IMAGE_PHOTO, MessageType.DOCUMENT_TEXT]:
            return ProcessingPriority.NORMAL
        
        # Low priority
        if message_type in [MessageType.AUDIO_MUSIC, MessageType.VIDEO_FILE]:
            return ProcessingPriority.LOW
        
        # Background priority
        if message_type.value.startswith("system_"):
            return ProcessingPriority.BACKGROUND
        
        return ProcessingPriority.NORMAL
    
    def _select_routing_strategy(
        self,
        message_type: MessageType,
        analysis_results: Dict[str, Any]
    ) -> RoutingStrategy:
        """Select routing strategy based on message type"""
        
        # Parallel processing for complex content
        if message_type in [MessageType.IMAGE_PHOTO, MessageType.DOCUMENT_CODE]:
            return RoutingStrategy.PARALLEL
        
        # Sequential for multi-step processing
        if message_type in [MessageType.TEXT_TECHNICAL, MessageType.AUDIO_VOICE]:
            return RoutingStrategy.SEQUENTIAL
        
        # Direct routing for simple content
        return RoutingStrategy.DIRECT
    
    async def _map_to_handlers(
        self,
        message_type: MessageType,
        analysis_results: Dict[str, Any],
        context: MessageContext
    ) -> Tuple[str, List[str]]:
        """Map message type to appropriate handlers"""
        
        # Get handler mapping for message type
        handler_config = self.handler_mappings.get(message_type, {})
        primary = handler_config.get("primary", "general_handler")
        secondary = handler_config.get("secondary", [])
        
        # Enhance based on analysis results
        content_features = analysis_results.get("content_features", {})
        
        # Add specialized handlers based on content
        if content_features.get("has_code", False):
            if "code_analysis_handler" not in secondary:
                secondary.append("code_analysis_handler")
        
        if content_features.get("has_urls", False):
            if "web_content_handler" not in secondary:
                secondary.append("web_content_handler")
        
        return primary, secondary
    
    async def _generate_handler_config(
        self,
        message_type: MessageType,
        analysis_results: Dict[str, Any],
        context: MessageContext
    ) -> Dict[str, Any]:
        """Generate configuration for message handlers"""
        config = {
            "message_type": message_type.value,
            "analysis_results": analysis_results,
            "processing_hints": context.processing_hints
        }
        
        # Add type-specific configuration
        content_features = analysis_results.get("content_features", {})
        
        if message_type == MessageType.TEXT_TECHNICAL:
            config.update({
                "enable_code_highlighting": True,
                "detailed_explanation": True,
                "include_examples": True
            })
        
        elif message_type == MessageType.TEXT_CREATIVE:
            config.update({
                "creative_mode": True,
                "encourage_imagination": True,
                "varied_responses": True
            })
        
        elif message_type in [MessageType.IMAGE_PHOTO, MessageType.IMAGE_SCREENSHOT]:
            config.update({
                "perform_ocr": True,
                "analyze_visual_elements": True,
                "describe_accessibility": True
            })
        
        return config
    
    def _estimate_processing_time(
        self,
        message_type: MessageType,
        analysis_results: Dict[str, Any]
    ) -> float:
        """Estimate processing time based on message complexity"""
        
        base_times = {
            MessageType.TEXT_CASUAL: 0.5,
            MessageType.TEXT_QUESTION: 1.0,
            MessageType.TEXT_COMMAND: 0.3,
            MessageType.TEXT_TECHNICAL: 2.0,
            MessageType.TEXT_CREATIVE: 1.5,
            MessageType.IMAGE_PHOTO: 3.0,
            MessageType.IMAGE_SCREENSHOT: 4.0,
            MessageType.DOCUMENT_CODE: 5.0,
            MessageType.AUDIO_VOICE: 10.0,
            MessageType.VIDEO_FILE: 15.0
        }
        
        base_time = base_times.get(message_type, 2.0)
        
        # Adjust based on content complexity
        content_features = analysis_results.get("content_features", {})
        
        # Longer content takes more time
        length_factor = min(content_features.get("length", 100) / 1000, 2.0)
        
        # Technical content takes more time
        technical_factor = 1 + content_features.get("technical_score", 0)
        
        return base_time * (1 + length_factor) * technical_factor
    
    def _requires_special_handling(
        self,
        message_type: MessageType,
        analysis_results: Dict[str, Any]
    ) -> bool:
        """Determine if message requires special handling"""
        
        # Media content requires special handling
        if message_type.value.startswith(("image_", "video_", "audio_", "document_")):
            return True
        
        # Large text content
        content_features = analysis_results.get("content_features", {})
        if content_features.get("length", 0) > 2000:
            return True
        
        # Code content
        if content_features.get("has_code", False):
            return True
        
        return False
    
    def _initialize_type_signatures(self) -> Dict[MessageType, TypeSignature]:
        """Initialize message type signatures for classification"""
        # This would contain detailed patterns for each message type
        # Simplified version for brevity
        return {}
    
    def _initialize_handler_mappings(self) -> Dict[MessageType, Dict[str, Any]]:
        """Initialize handler mappings for different message types"""
        return {
            MessageType.TEXT_CASUAL: {
                "primary": "conversation_handler",
                "secondary": ["sentiment_handler"]
            },
            MessageType.TEXT_QUESTION: {
                "primary": "qa_handler",
                "secondary": ["search_handler", "knowledge_handler"]
            },
            MessageType.TEXT_COMMAND: {
                "primary": "command_handler",
                "secondary": []
            },
            MessageType.TEXT_TECHNICAL: {
                "primary": "technical_handler",
                "secondary": ["code_handler", "documentation_handler"]
            },
            MessageType.TEXT_CREATIVE: {
                "primary": "creative_handler",
                "secondary": ["generation_handler"]
            },
            MessageType.IMAGE_PHOTO: {
                "primary": "image_analysis_handler",
                "secondary": ["ocr_handler", "description_handler"]
            },
            MessageType.DOCUMENT_CODE: {
                "primary": "code_analysis_handler",
                "secondary": ["syntax_handler", "review_handler"]
            }
        }
    
    def _initialize_language_patterns(self) -> Dict[str, List[str]]:
        """Initialize language detection patterns"""
        return {
            "english": [r'\b(the|and|or|but|in|on|at)\b'],
            "python": [r'\b(def|class|import|print)\b'],
            "javascript": [r'\b(function|var|console)\b']
        }
    
    def _record_classification(
        self,
        message_type: MessageType,
        confidence: float,
        analysis_results: Dict[str, Any]
    ) -> None:
        """Record classification for learning purposes"""
        record = {
            "timestamp": time.time(),
            "message_type": message_type.value,
            "confidence": confidence,
            "features": analysis_results.get("content_features", {})
        }
        
        self.classification_history.append(record)
        
        # Keep only recent history
        if len(self.classification_history) > 1000:
            self.classification_history = self.classification_history[-500:]
    
    def _update_statistics(self, confidence: float, processing_time: float) -> None:
        """Update routing statistics"""
        self.avg_confidence = (
            (self.avg_confidence * (self.total_classifications - 1) + confidence) 
            / self.total_classifications
        )
        
        self.processing_times.append(processing_time * 1000)  # Convert to ms
        if len(self.processing_times) > 1000:
            self.processing_times = self.processing_times[-500:]
    
    async def provide_feedback(
        self,
        message_type: MessageType,
        handler_performance: Dict[str, float],
        user_satisfaction: float
    ) -> None:
        """Provide feedback for learning improvement"""
        if self.enable_learning:
            # Record feedback
            self.feedback_data[message_type.value].append(user_satisfaction)
            
            # Update handler performance
            for handler, performance in handler_performance.items():
                if handler not in self.handler_performance[message_type.value]:
                    self.handler_performance[message_type.value][handler] = []
                self.handler_performance[message_type.value][handler] = performance
    
    async def get_status(self) -> Dict[str, Any]:
        """Get router status and statistics"""
        avg_processing_time = (
            sum(self.processing_times) / len(self.processing_times)
            if self.processing_times else 0.0
        )
        
        # Calculate type distribution
        type_counts = defaultdict(int)
        for record in self.classification_history[-100:]:  # Last 100 classifications
            type_counts[record["message_type"]] += 1
        
        return {
            "total_classifications": self.total_classifications,
            "avg_confidence": self.avg_confidence,
            "avg_processing_time_ms": avg_processing_time,
            "type_distribution": dict(type_counts),
            "feedback_records": len(self.feedback_data),
            "handler_mappings": len(self.handler_mappings),
            "learning_enabled": self.enable_learning
        }
    
    async def shutdown(self) -> None:
        """Gracefully shutdown the type router"""
        logger.info("Shutting down type router...")
        
        # Save learning data if needed
        if self.enable_learning and self.classification_history:
            logger.info(f"Preserving {len(self.classification_history)} classification records")
        
        logger.info("Type router shutdown complete")