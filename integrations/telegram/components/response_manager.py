"""Response Manager Component

This module handles response formatting, message splitting, media handling,
and delivery optimization for Telegram messages with comprehensive
output processing and presentation enhancement.
"""

import asyncio
import html
import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from enum import Enum

from pydantic import BaseModel, Field

from .context_builder import MessageContext
from .agent_orchestrator import AgentResult


logger = logging.getLogger(__name__)


class ResponseFormat(Enum):
    """Response format types"""
    PLAIN_TEXT = "plain_text"
    MARKDOWN = "markdown"
    HTML = "html"
    CODE_BLOCK = "code_block"
    STRUCTURED = "structured"


class MessageSplitStrategy(Enum):
    """Message splitting strategies for long content"""
    SENTENCE_BOUNDARY = "sentence_boundary"
    PARAGRAPH_BOUNDARY = "paragraph_boundary"
    CODE_BLOCK_BOUNDARY = "code_block_boundary"
    SEMANTIC_BOUNDARY = "semantic_boundary"
    HARD_LIMIT = "hard_limit"


class DeliveryMode(Enum):
    """Message delivery modes"""
    IMMEDIATE = "immediate"         # Send immediately
    BATCHED = "batched"            # Batch multiple responses
    STREAMING = "streaming"         # Stream long responses
    PROGRESSIVE = "progressive"     # Progressive enhancement


class MediaType(Enum):
    """Supported media types for responses"""
    TEXT = "text"
    IMAGE = "image"
    DOCUMENT = "document"
    AUDIO = "audio"
    VIDEO = "video"
    ANIMATION = "animation"
    STICKER = "sticker"


@dataclass
class MediaAttachment:
    """Media attachment information"""
    media_type: MediaType
    file_path: Optional[str] = None
    file_data: Optional[bytes] = None
    caption: Optional[str] = None
    filename: Optional[str] = None
    mime_type: Optional[str] = None
    thumbnail: Optional[bytes] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass 
class FormattingRules:
    """Rules for response formatting"""
    max_message_length: int = 4096
    preserve_code_formatting: bool = True
    enable_markdown: bool = True
    enable_html: bool = False
    auto_link_detection: bool = True
    emoji_enhancement: bool = False
    highlight_keywords: bool = True
    code_syntax_highlighting: bool = True
    table_formatting: bool = True
    list_formatting: bool = True


class FormattedResponse(BaseModel):
    """Formatted response ready for delivery"""
    
    # Content
    text: str = Field(..., description="Formatted text content")
    format_type: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)
    
    # Message properties
    parse_mode: Optional[str] = Field(None, description="Telegram parse mode")
    disable_web_page_preview: bool = Field(default=False)
    disable_notification: bool = Field(default=False)
    
    # Media attachments
    media_attachments: List[MediaAttachment] = Field(default_factory=list)
    
    # Delivery options
    reply_to_message_id: Optional[int] = None
    chat_id: Optional[int] = None
    delivery_mode: DeliveryMode = Field(default=DeliveryMode.IMMEDIATE)
    
    # Splitting information
    is_split_message: bool = Field(default=False)
    split_index: int = Field(default=0)
    total_splits: int = Field(default=1)
    continuation_token: Optional[str] = None
    
    # Quality metrics
    readability_score: float = Field(default=0.8, ge=0.0, le=1.0)
    formatting_quality: float = Field(default=0.8, ge=0.0, le=1.0)
    estimated_read_time: float = Field(default=10.0)  # seconds
    
    # Metadata
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ResponseManager:
    """
    Advanced response manager that handles formatting, splitting, media processing,
    and delivery optimization for Telegram responses with comprehensive
    presentation enhancement and user experience optimization.
    """
    
    def __init__(
        self,
        formatting_rules: Optional[FormattingRules] = None,
        enable_smart_splitting: bool = True,
        enable_media_processing: bool = True,
        enable_progressive_delivery: bool = True,
        max_concurrent_formatting: int = 5,
        response_cache_size: int = 100,
        quality_enhancement: bool = True
    ):
        """
        Initialize the response manager.
        
        Args:
            formatting_rules: Rules for response formatting
            enable_smart_splitting: Enable intelligent message splitting
            enable_media_processing: Enable media attachment processing
            enable_progressive_delivery: Enable progressive response delivery
            max_concurrent_formatting: Maximum concurrent formatting tasks
            response_cache_size: Size of response formatting cache
            quality_enhancement: Enable response quality enhancement
        """
        self.formatting_rules = formatting_rules or FormattingRules()
        self.enable_smart_splitting = enable_smart_splitting
        self.enable_media_processing = enable_media_processing
        self.enable_progressive_delivery = enable_progressive_delivery
        self.quality_enhancement = quality_enhancement
        
        # Concurrency control
        self.formatting_semaphore = asyncio.Semaphore(max_concurrent_formatting)
        
        # Caching
        self.response_cache: Dict[str, List[FormattedResponse]] = {}
        self.cache_access_times: Dict[str, float] = {}
        self.response_cache_size = response_cache_size
        
        # Performance tracking
        self.formatting_count = 0
        self.split_message_count = 0
        self.media_processing_count = 0
        self.avg_formatting_time = 0.0
        
        # Content processors
        self.code_highlighter = self._initialize_code_highlighter()
        self.markdown_processor = self._initialize_markdown_processor()
        self.emoji_enhancer = self._initialize_emoji_enhancer()
        
        # Quality assessment
        self.readability_metrics: List[float] = []
        self.user_engagement_metrics: Dict[str, List[float]] = defaultdict(list)
        
        logger.info(
            f"ResponseManager initialized with smart_splitting={enable_smart_splitting}, "
            f"media_processing={enable_media_processing}, "
            f"max_length={self.formatting_rules.max_message_length}"
        )
    
    async def format_response(
        self,
        agent_result: AgentResult,
        context: MessageContext,
        target_chat_id: int,
        reply_to_message_id: Optional[int] = None
    ) -> List[FormattedResponse]:
        """
        Format agent result into optimized Telegram responses.
        
        Args:
            agent_result: Result from agent orchestration
            context: Message context
            target_chat_id: Target chat ID for response
            reply_to_message_id: Optional message ID to reply to
            
        Returns:
            List of formatted responses ready for delivery
        """
        start_time = time.perf_counter()
        
        async with self.formatting_semaphore:
            try:
                self.formatting_count += 1
                
                # Check cache first
                cache_key = self._generate_cache_key(agent_result, context)
                if cache_key in self.response_cache:
                    self.cache_access_times[cache_key] = time.time()
                    cached_responses = self.response_cache[cache_key]
                    
                    # Update chat_id and reply info
                    for response in cached_responses:
                        response.chat_id = target_chat_id
                        response.reply_to_message_id = reply_to_message_id
                    
                    return cached_responses
                
                logger.debug(
                    f"Formatting response for chat {target_chat_id}, "
                    f"agent: {agent_result.agent_name}"
                )
                
                # Determine optimal formatting strategy
                format_strategy = await self._determine_format_strategy(
                    agent_result, context
                )
                
                # Process primary response
                primary_responses = await self._process_primary_response(
                    agent_result.primary_response,
                    format_strategy,
                    context,
                    target_chat_id,
                    reply_to_message_id
                )
                
                # Process supplementary responses
                supplementary_responses = []
                if agent_result.supplementary_responses:
                    for agent_id, response_text in agent_result.supplementary_responses.items():
                        supp_responses = await self._process_supplementary_response(
                            response_text,
                            agent_id,
                            format_strategy,
                            context,
                            target_chat_id
                        )
                        supplementary_responses.extend(supp_responses)
                
                # Process tool outputs as media if applicable
                media_responses = []
                if self.enable_media_processing and agent_result.tool_outputs:
                    media_responses = await self._process_tool_outputs_as_media(
                        agent_result.tool_outputs,
                        context,
                        target_chat_id
                    )
                
                # Combine all responses
                all_responses = primary_responses + supplementary_responses + media_responses
                
                # Apply quality enhancement
                if self.quality_enhancement:
                    all_responses = await self._enhance_response_quality(
                        all_responses, context, agent_result
                    )
                
                # Optimize delivery order
                all_responses = self._optimize_delivery_order(all_responses)
                
                # Cache responses
                if len(all_responses) <= 5:  # Don't cache very long response chains
                    self._cache_responses(cache_key, all_responses)
                
                # Update performance metrics
                formatting_time = time.perf_counter() - start_time
                self._update_performance_metrics(formatting_time, len(all_responses))
                
                logger.debug(
                    f"Response formatted in {formatting_time:.2f}s, "
                    f"generated {len(all_responses)} messages"
                )
                
                return all_responses
                
            except Exception as e:
                logger.error(f"Response formatting error: {str(e)}", exc_info=True)
                
                # Return fallback response
                fallback_response = FormattedResponse(
                    text="I apologize, but I encountered an error formatting my response. Please try again.",
                    chat_id=target_chat_id,
                    reply_to_message_id=reply_to_message_id,
                    metadata={"error": str(e), "fallback": True}
                )
                
                return [fallback_response]
    
    async def _determine_format_strategy(
        self,
        agent_result: AgentResult,
        context: MessageContext
    ) -> Dict[str, Any]:
        """Determine optimal formatting strategy"""
        
        strategy = {
            "primary_format": ResponseFormat.MARKDOWN,
            "split_strategy": MessageSplitStrategy.PARAGRAPH_BOUNDARY,
            "delivery_mode": DeliveryMode.IMMEDIATE,
            "enable_code_highlighting": False,
            "enable_structured_output": False,
            "media_handling": "inline"
        }
        
        # Analyze content for format decisions
        content = agent_result.primary_response
        
        # Code content detection
        if re.search(r'```[\s\S]*?```|`[^`]+`', content):
            strategy["enable_code_highlighting"] = True
            strategy["split_strategy"] = MessageSplitStrategy.CODE_BLOCK_BOUNDARY
        
        # Long content handling
        if len(content) > self.formatting_rules.max_message_length:
            if self.enable_smart_splitting:
                strategy["split_strategy"] = MessageSplitStrategy.SEMANTIC_BOUNDARY
            else:
                strategy["split_strategy"] = MessageSplitStrategy.HARD_LIMIT
        
        # Technical content
        if any(tool in ["code_execution", "image_analysis"] for tool in agent_result.tools_used):
            strategy["enable_structured_output"] = True
            strategy["primary_format"] = ResponseFormat.STRUCTURED
        
        # User preferences from context
        if context.processing_hints:
            preferred_length = context.processing_hints.get("preferred_response_length", "medium")
            if preferred_length == "short":
                strategy["delivery_mode"] = DeliveryMode.IMMEDIATE
            elif preferred_length == "long":
                strategy["delivery_mode"] = DeliveryMode.PROGRESSIVE
        
        return strategy
    
    async def _process_primary_response(
        self,
        response_text: str,
        format_strategy: Dict[str, Any],
        context: MessageContext,
        target_chat_id: int,
        reply_to_message_id: Optional[int]
    ) -> List[FormattedResponse]:
        """Process and format the primary response"""
        
        # Apply content enhancement
        enhanced_text = await self._enhance_content(
            response_text, format_strategy, context
        )
        
        # Apply formatting
        formatted_text = await self._apply_formatting(
            enhanced_text, format_strategy
        )
        
        # Split if necessary
        if self.enable_smart_splitting and len(formatted_text) > self.formatting_rules.max_message_length:
            return await self._split_message(
                formatted_text,
                format_strategy,
                target_chat_id,
                reply_to_message_id
            )
        
        # Create single response
        response = FormattedResponse(
            text=formatted_text,
            format_type=format_strategy["primary_format"],
            parse_mode="Markdown" if format_strategy["primary_format"] == ResponseFormat.MARKDOWN else None,
            chat_id=target_chat_id,
            reply_to_message_id=reply_to_message_id,
            delivery_mode=format_strategy["delivery_mode"]
        )
        
        # Calculate quality metrics
        response.readability_score = self._calculate_readability(formatted_text)
        response.formatting_quality = self._assess_formatting_quality(formatted_text)
        response.estimated_read_time = self._estimate_read_time(formatted_text)
        
        return [response]
    
    async def _process_supplementary_response(
        self,
        response_text: str,
        agent_id: str,
        format_strategy: Dict[str, Any],
        context: MessageContext,
        target_chat_id: int
    ) -> List[FormattedResponse]:
        """Process supplementary responses from parallel agents"""
        
        # Add agent attribution
        attributed_text = f"**Additional insight from {agent_id}:**\n\n{response_text}"
        
        # Format and process similar to primary response
        enhanced_text = await self._enhance_content(
            attributed_text, format_strategy, context
        )
        
        formatted_text = await self._apply_formatting(enhanced_text, format_strategy)
        
        response = FormattedResponse(
            text=formatted_text,
            format_type=format_strategy["primary_format"],
            parse_mode="Markdown",
            chat_id=target_chat_id,
            delivery_mode=DeliveryMode.BATCHED,  # Supplementary responses are batched
            metadata={"source_agent": agent_id, "type": "supplementary"}
        )
        
        return [response]
    
    async def _process_tool_outputs_as_media(
        self,
        tool_outputs: Dict[str, Any],
        context: MessageContext,
        target_chat_id: int
    ) -> List[FormattedResponse]:
        """Process tool outputs as media attachments or structured responses"""
        
        media_responses = []
        self.media_processing_count += 1
        
        for tool_name, output in tool_outputs.items():
            try:
                if tool_name == "image_generation" and isinstance(output, dict):
                    # Handle generated images
                    if "image_path" in output or "image_data" in output:
                        media_attachment = MediaAttachment(
                            media_type=MediaType.IMAGE,
                            file_path=output.get("image_path"),
                            file_data=output.get("image_data"),
                            caption=output.get("description", "Generated image"),
                            metadata={"tool": tool_name, "generation_params": output}
                        )
                        
                        response = FormattedResponse(
                            text=output.get("description", "Generated image"),
                            chat_id=target_chat_id,
                            media_attachments=[media_attachment],
                            delivery_mode=DeliveryMode.IMMEDIATE,
                            metadata={"tool_output": True, "tool": tool_name}
                        )
                        media_responses.append(response)
                
                elif tool_name == "image_analysis" and isinstance(output, dict):
                    # Handle image analysis results
                    analysis_text = self._format_image_analysis(output)
                    
                    response = FormattedResponse(
                        text=analysis_text,
                        format_type=ResponseFormat.STRUCTURED,
                        parse_mode="Markdown",
                        chat_id=target_chat_id,
                        delivery_mode=DeliveryMode.IMMEDIATE,
                        metadata={"tool_output": True, "tool": tool_name}
                    )
                    media_responses.append(response)
                
                elif tool_name == "code_execution" and isinstance(output, dict):
                    # Handle code execution results
                    code_text = self._format_code_execution(output)
                    
                    response = FormattedResponse(
                        text=code_text,
                        format_type=ResponseFormat.CODE_BLOCK,
                        parse_mode="Markdown",
                        chat_id=target_chat_id,
                        delivery_mode=DeliveryMode.IMMEDIATE,
                        metadata={"tool_output": True, "tool": tool_name}
                    )
                    media_responses.append(response)
                
            except Exception as e:
                logger.warning(f"Failed to process {tool_name} output as media: {e}")
                continue
        
        return media_responses
    
    async def _enhance_content(
        self,
        text: str,
        format_strategy: Dict[str, Any],
        context: MessageContext
    ) -> str:
        """Enhance content quality and presentation"""
        
        enhanced = text
        
        # Fix common formatting issues
        enhanced = self._fix_common_formatting_issues(enhanced)
        
        # Enhance code blocks
        if format_strategy.get("enable_code_highlighting"):
            enhanced = self._enhance_code_blocks(enhanced)
        
        # Add emoji enhancement if enabled
        if self.formatting_rules.emoji_enhancement:
            enhanced = self._add_emoji_enhancement(enhanced, context)
        
        # Improve list formatting
        if self.formatting_rules.list_formatting:
            enhanced = self._improve_list_formatting(enhanced)
        
        # Enhance table formatting
        if self.formatting_rules.table_formatting:
            enhanced = self._enhance_table_formatting(enhanced)
        
        # Add keyword highlighting
        if self.formatting_rules.highlight_keywords:
            enhanced = self._highlight_keywords(enhanced, context)
        
        return enhanced
    
    async def _apply_formatting(
        self,
        text: str,
        format_strategy: Dict[str, Any]
    ) -> str:
        """Apply formatting based on strategy"""
        
        formatted = text
        
        if format_strategy["primary_format"] == ResponseFormat.MARKDOWN:
            formatted = self._apply_markdown_formatting(formatted)
        elif format_strategy["primary_format"] == ResponseFormat.HTML:
            formatted = self._apply_html_formatting(formatted)
        elif format_strategy["primary_format"] == ResponseFormat.CODE_BLOCK:
            formatted = self._apply_code_block_formatting(formatted)
        elif format_strategy["primary_format"] == ResponseFormat.STRUCTURED:
            formatted = self._apply_structured_formatting(formatted)
        
        # Apply link detection
        if self.formatting_rules.auto_link_detection:
            formatted = self._apply_auto_link_detection(formatted)
        
        return formatted
    
    async def _split_message(
        self,
        text: str,
        format_strategy: Dict[str, Any],
        target_chat_id: int,
        reply_to_message_id: Optional[int]
    ) -> List[FormattedResponse]:
        """Split long messages intelligently"""
        
        self.split_message_count += 1
        split_strategy = format_strategy["split_strategy"]
        max_length = self.formatting_rules.max_message_length - 100  # Safety margin
        
        splits = []
        
        if split_strategy == MessageSplitStrategy.SEMANTIC_BOUNDARY:
            splits = self._split_by_semantic_boundaries(text, max_length)
        elif split_strategy == MessageSplitStrategy.PARAGRAPH_BOUNDARY:
            splits = self._split_by_paragraphs(text, max_length)
        elif split_strategy == MessageSplitStrategy.CODE_BLOCK_BOUNDARY:
            splits = self._split_by_code_blocks(text, max_length)
        elif split_strategy == MessageSplitStrategy.SENTENCE_BOUNDARY:
            splits = self._split_by_sentences(text, max_length)
        else:  # HARD_LIMIT
            splits = self._split_by_hard_limit(text, max_length)
        
        # Create formatted responses for each split
        responses = []
        total_splits = len(splits)
        
        for i, split_text in enumerate(splits):
            response = FormattedResponse(
                text=split_text,
                format_type=format_strategy["primary_format"],
                parse_mode="Markdown" if format_strategy["primary_format"] == ResponseFormat.MARKDOWN else None,
                chat_id=target_chat_id,
                reply_to_message_id=reply_to_message_id if i == 0 else None,
                delivery_mode=DeliveryMode.PROGRESSIVE if i > 0 else DeliveryMode.IMMEDIATE,
                is_split_message=True,
                split_index=i,
                total_splits=total_splits,
                metadata={
                    "split_strategy": split_strategy.value,
                    "continuation_indicator": f"({i+1}/{total_splits})" if total_splits > 1 else None
                }
            )
            
            responses.append(response)
        
        return responses
    
    def _split_by_semantic_boundaries(self, text: str, max_length: int) -> List[str]:
        """Split text by semantic boundaries (paragraphs, then sentences)"""
        
        if len(text) <= max_length:
            return [text]
        
        # First try paragraph boundaries
        paragraphs = text.split('\n\n')
        if len(paragraphs) > 1:
            splits = []
            current_split = ""
            
            for para in paragraphs:
                if len(current_split + para + "\n\n") <= max_length:
                    current_split += para + "\n\n"
                else:
                    if current_split:
                        splits.append(current_split.strip())
                        current_split = para + "\n\n"
                    else:
                        # Paragraph too long, split by sentences
                        sentence_splits = self._split_by_sentences(para, max_length)
                        splits.extend(sentence_splits)
            
            if current_split:
                splits.append(current_split.strip())
            
            return splits
        
        # Fall back to sentence splitting
        return self._split_by_sentences(text, max_length)
    
    def _split_by_paragraphs(self, text: str, max_length: int) -> List[str]:
        """Split text by paragraph boundaries"""
        
        paragraphs = text.split('\n\n')
        splits = []
        current_split = ""
        
        for para in paragraphs:
            if len(current_split + para + "\n\n") <= max_length:
                current_split += para + "\n\n"
            else:
                if current_split:
                    splits.append(current_split.strip())
                
                if len(para) <= max_length:
                    current_split = para + "\n\n"
                else:
                    # Paragraph too long, hard split
                    hard_splits = self._split_by_hard_limit(para, max_length)
                    splits.extend(hard_splits[:-1])
                    current_split = hard_splits[-1] + "\n\n"
        
        if current_split:
            splits.append(current_split.strip())
        
        return splits or [text]
    
    def _split_by_code_blocks(self, text: str, max_length: int) -> List[str]:
        """Split text preserving code block boundaries"""
        
        # Find code blocks
        code_block_pattern = r'```[\s\S]*?```'
        
        parts = []
        last_end = 0
        
        for match in re.finditer(code_block_pattern, text):
            # Add text before code block
            if match.start() > last_end:
                parts.append(("text", text[last_end:match.start()]))
            
            # Add code block
            parts.append(("code", match.group()))
            last_end = match.end()
        
        # Add remaining text
        if last_end < len(text):
            parts.append(("text", text[last_end:]))
        
        # Combine parts into splits
        splits = []
        current_split = ""
        
        for part_type, content in parts:
            if len(current_split + content) <= max_length:
                current_split += content
            else:
                if current_split:
                    splits.append(current_split)
                
                if part_type == "code" or len(content) <= max_length:
                    current_split = content
                else:
                    # Split long text part
                    text_splits = self._split_by_sentences(content, max_length)
                    splits.extend(text_splits[:-1])
                    current_split = text_splits[-1]
        
        if current_split:
            splits.append(current_split)
        
        return splits or [text]
    
    def _split_by_sentences(self, text: str, max_length: int) -> List[str]:
        """Split text by sentence boundaries"""
        
        sentences = re.split(r'(?<=[.!?])\s+', text)
        splits = []
        current_split = ""
        
        for sentence in sentences:
            if len(current_split + sentence + " ") <= max_length:
                current_split += sentence + " "
            else:
                if current_split:
                    splits.append(current_split.strip())
                
                if len(sentence) <= max_length:
                    current_split = sentence + " "
                else:
                    # Sentence too long, hard split
                    hard_splits = self._split_by_hard_limit(sentence, max_length)
                    splits.extend(hard_splits[:-1])
                    current_split = hard_splits[-1] + " "
        
        if current_split:
            splits.append(current_split.strip())
        
        return splits or [text]
    
    def _split_by_hard_limit(self, text: str, max_length: int) -> List[str]:
        """Split text by hard character limit"""
        
        splits = []
        for i in range(0, len(text), max_length):
            splits.append(text[i:i + max_length])
        
        return splits
    
    async def _enhance_response_quality(
        self,
        responses: List[FormattedResponse],
        context: MessageContext,
        agent_result: AgentResult
    ) -> List[FormattedResponse]:
        """Enhance overall response quality"""
        
        enhanced_responses = []
        
        for response in responses:
            # Improve readability
            if response.readability_score < 0.6:
                response.text = self._improve_readability(response.text)
                response.readability_score = self._calculate_readability(response.text)
            
            # Add context-aware enhancements
            if context.user_profile:
                response.text = self._add_personalization(
                    response.text, context.user_profile
                )
            
            # Add helpful metadata
            response.metadata.update({
                "context_chat_id": context.chat_id,
                "processing_time": agent_result.total_execution_time,
                "quality_enhanced": True
            })
            
            enhanced_responses.append(response)
        
        return enhanced_responses
    
    def _optimize_delivery_order(
        self,
        responses: List[FormattedResponse]
    ) -> List[FormattedResponse]:
        """Optimize the order of response delivery"""
        
        # Sort by delivery priority
        priority_order = {
            DeliveryMode.IMMEDIATE: 0,
            DeliveryMode.BATCHED: 1,
            DeliveryMode.PROGRESSIVE: 2,
            DeliveryMode.STREAMING: 3
        }
        
        return sorted(
            responses,
            key=lambda r: (
                priority_order.get(r.delivery_mode, 4),
                r.split_index if r.is_split_message else 0
            )
        )
    
    def _fix_common_formatting_issues(self, text: str) -> str:
        """Fix common formatting issues"""
        
        # Fix multiple consecutive newlines
        text = re.sub(r'\n{3,}', '\n\n', text)
        
        # Fix spacing around punctuation
        text = re.sub(r'\s+([,.!?;:])', r'\1', text)
        text = re.sub(r'([.!?])\s*([A-Z])', r'\1 \2', text)
        
        # Fix list formatting
        text = re.sub(r'^(\s*)-\s+', r'\1• ', text, flags=re.MULTILINE)
        
        # Remove trailing whitespace
        text = '\n'.join(line.rstrip() for line in text.split('\n'))
        
        return text.strip()
    
    def _enhance_code_blocks(self, text: str) -> str:
        """Enhance code block formatting"""
        
        # Add language hints where missing
        code_block_pattern = r'```\n((?:(?!```)[\s\S])*?)```'
        
        def enhance_block(match):
            code_content = match.group(1)
            
            # Try to detect language
            if code_content.strip().startswith(('def ', 'import ', 'from ', 'class ')):
                return f'```python\n{code_content}```'
            elif 'function' in code_content or 'const ' in code_content or 'let ' in code_content:
                return f'```javascript\n{code_content}```'
            elif code_content.strip().startswith(('SELECT', 'INSERT', 'UPDATE', 'DELETE')):
                return f'```sql\n{code_content}```'
            else:
                return f'```\n{code_content}```'
        
        return re.sub(code_block_pattern, enhance_block, text)
    
    def _add_emoji_enhancement(self, text: str, context: MessageContext) -> str:
        """Add appropriate emoji enhancement"""
        
        # Simple emoji mapping for common concepts
        emoji_map = {
            r'\b(error|problem|issue)\b': '⚠️',
            r'\b(success|complete|done)\b': '✅',
            r'\b(tip|hint|suggestion)\b': '💡',
            r'\b(important|note)\b': '📌',
            r'\b(code|programming)\b': '💻',
            r'\b(image|photo)\b': '🖼️'
        }
        
        enhanced = text
        for pattern, emoji in emoji_map.items():
            enhanced = re.sub(pattern, f'{emoji} \\g<0>', enhanced, flags=re.IGNORECASE)
        
        return enhanced
    
    def _improve_list_formatting(self, text: str) -> str:
        """Improve list formatting"""
        
        # Convert numbered lists to proper format
        text = re.sub(r'^(\d+)\.\s+', r'\1. ', text, flags=re.MULTILINE)
        
        # Ensure consistent bullet points
        text = re.sub(r'^[*-]\s+', '• ', text, flags=re.MULTILINE)
        
        return text
    
    def _enhance_table_formatting(self, text: str) -> str:
        """Enhance table formatting"""
        
        # Simple table detection and formatting
        lines = text.split('\n')
        in_table = False
        enhanced_lines = []
        
        for line in lines:
            if '|' in line and line.count('|') >= 2:
                # Potential table row
                if not in_table:
                    in_table = True
                
                # Clean up table formatting
                parts = [part.strip() for part in line.split('|')]
                if parts[0] == '':
                    parts = parts[1:]
                if parts[-1] == '':
                    parts = parts[:-1]
                
                enhanced_line = '| ' + ' | '.join(parts) + ' |'
                enhanced_lines.append(enhanced_line)
            else:
                in_table = False
                enhanced_lines.append(line)
        
        return '\n'.join(enhanced_lines)
    
    def _highlight_keywords(self, text: str, context: MessageContext) -> str:
        """Highlight important keywords based on context"""
        
        # Get keywords from user expertise
        if context.user_profile and context.user_profile.expertise_areas:
            for keyword in context.user_profile.expertise_areas:
                pattern = r'\b' + re.escape(keyword) + r'\b'
                text = re.sub(pattern, f'**{keyword}**', text, flags=re.IGNORECASE)
        
        return text
    
    def _apply_markdown_formatting(self, text: str) -> str:
        """Apply Telegram-compatible markdown formatting"""
        
        # Escape special characters but preserve intentional formatting
        # This is a simplified version - in production you'd use a proper markdown processor
        return text
    
    def _apply_html_formatting(self, text: str) -> str:
        """Apply HTML formatting"""
        
        # Convert markdown-style formatting to HTML
        text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
        text = re.sub(r'\*(.*?)\*', r'<i>\1</i>', text)
        text = re.sub(r'`(.*?)`', r'<code>\1</code>', text)
        
        # Escape other HTML characters
        text = html.escape(text, quote=False)
        
        return text
    
    def _apply_code_block_formatting(self, text: str) -> str:
        """Apply code block formatting"""
        
        # Wrap entire content in code block if not already
        if not text.startswith('```'):
            # Try to detect language
            if 'def ' in text or 'import ' in text:
                return f'```python\n{text}\n```'
            else:
                return f'```\n{text}\n```'
        
        return text
    
    def _apply_structured_formatting(self, text: str) -> str:
        """Apply structured formatting for technical content"""
        
        # Add section headers
        lines = text.split('\n')
        structured_lines = []
        
        for line in lines:
            if line.strip() and not line.startswith(('•', '-', '*', '1.', '2.')):
                if len(line) < 50 and line.endswith(':'):
                    # Potential section header
                    structured_lines.append(f'**{line}**')
                else:
                    structured_lines.append(line)
            else:
                structured_lines.append(line)
        
        return '\n'.join(structured_lines)
    
    def _apply_auto_link_detection(self, text: str) -> str:
        """Apply automatic link detection and formatting"""
        
        # URL pattern
        url_pattern = r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'
        
        def format_url(match):
            url = match.group(0)
            # Simple domain extraction for display
            domain = re.sub(r'https?://(www\.)?([^/]+).*', r'\2', url)
            return f'[{domain}]({url})'
        
        return re.sub(url_pattern, format_url, text)
    
    def _format_image_analysis(self, output: Dict[str, Any]) -> str:
        """Format image analysis output"""
        
        formatted = "**Image Analysis Results:**\n\n"
        
        if "description" in output:
            formatted += f"**Description:** {output['description']}\n\n"
        
        if "objects" in output:
            formatted += "**Detected Objects:**\n"
            for obj in output["objects"]:
                formatted += f"• {obj}\n"
            formatted += "\n"
        
        if "text" in output:
            formatted += f"**Extracted Text:**\n```\n{output['text']}\n```\n\n"
        
        if "safety_analysis" in output:
            safety = output["safety_analysis"]
            formatted += f"**Safety Score:** {safety.get('score', 'N/A')}/10\n"
        
        return formatted.strip()
    
    def _format_code_execution(self, output: Dict[str, Any]) -> str:
        """Format code execution output"""
        
        formatted = "**Code Execution Results:**\n\n"
        
        if "stdout" in output and output["stdout"]:
            formatted += "**Output:**\n```\n" + output["stdout"] + "\n```\n\n"
        
        if "stderr" in output and output["stderr"]:
            formatted += "**Errors:**\n```\n" + output["stderr"] + "\n```\n\n"
        
        if "return_value" in output:
            formatted += f"**Return Value:** `{output['return_value']}`\n\n"
        
        if "execution_time" in output:
            formatted += f"**Execution Time:** {output['execution_time']:.2f}s\n"
        
        return formatted.strip()
    
    def _calculate_readability(self, text: str) -> float:
        """Calculate readability score"""
        
        words = len(text.split())
        sentences = len(re.split(r'[.!?]+', text))
        
        if words == 0 or sentences == 0:
            return 0.5
        
        avg_words_per_sentence = words / sentences
        
        # Simple readability score (lower is better readability, but we invert for 0-1 scale)
        if avg_words_per_sentence <= 15:
            return 0.9
        elif avg_words_per_sentence <= 25:
            return 0.7
        else:
            return 0.5
    
    def _assess_formatting_quality(self, text: str) -> float:
        """Assess formatting quality"""
        
        score = 0.5  # Base score
        
        # Check for good markdown usage
        if re.search(r'\*\*.*?\*\*', text):  # Bold formatting
            score += 0.1
        
        if re.search(r'```[\s\S]*?```', text):  # Code blocks
            score += 0.1
        
        # Check for lists
        if re.search(r'^[•\-\*]\s+', text, re.MULTILINE):
            score += 0.1
        
        # Check for proper paragraph structure
        if '\n\n' in text:
            score += 0.1
        
        # Check for consistent formatting
        consistent_bullets = len(set(re.findall(r'^([•\-\*])\s+', text, re.MULTILINE))) <= 1
        if consistent_bullets:
            score += 0.1
        
        return min(score, 1.0)
    
    def _estimate_read_time(self, text: str) -> float:
        """Estimate reading time in seconds"""
        
        words = len(text.split())
        # Average reading speed: 200-250 words per minute
        return (words / 225) * 60
    
    def _improve_readability(self, text: str) -> str:
        """Improve text readability"""
        
        # Break up long sentences
        sentences = re.split(r'([.!?]+)', text)
        improved_sentences = []
        
        for i in range(0, len(sentences), 2):
            if i + 1 < len(sentences):
                sentence = sentences[i]
                punctuation = sentences[i + 1]
                
                # If sentence is very long, try to break it
                if len(sentence.split()) > 30 and ' and ' in sentence:
                    parts = sentence.split(' and ', 1)
                    improved_sentences.append(parts[0] + punctuation)
                    improved_sentences.append(' ' + parts[1] + punctuation)
                else:
                    improved_sentences.append(sentence + punctuation)
            else:
                improved_sentences.append(sentences[i])
        
        return ''.join(improved_sentences)
    
    def _add_personalization(
        self,
        text: str,
        user_profile
    ) -> str:
        """Add personalization based on user profile"""
        
        # Simple personalization based on expertise
        if hasattr(user_profile, 'expertise_areas'):
            if 'python' in user_profile.expertise_areas:
                # Add more technical detail for Python experts
                text = re.sub(
                    r'\bPython\b',
                    'Python (which you know well)',
                    text,
                    count=1
                )
        
        return text
    
    def _generate_cache_key(
        self,
        agent_result: AgentResult,
        context: MessageContext
    ) -> str:
        """Generate cache key for response"""
        
        import hashlib
        
        key_components = [
            agent_result.agent_name,
            agent_result.primary_response[:100],  # First 100 chars
            str(sorted(agent_result.tools_used)),
            str(context.message_type) if hasattr(context, 'message_type') else ""
        ]
        
        key_string = '|'.join(key_components)
        return hashlib.md5(key_string.encode()).hexdigest()[:12]
    
    def _cache_responses(
        self,
        cache_key: str,
        responses: List[FormattedResponse]
    ) -> None:
        """Cache formatted responses"""
        
        # Clean old cache entries if at limit
        if len(self.response_cache) >= self.response_cache_size:
            # Remove oldest entries
            oldest_key = min(self.cache_access_times.keys(), 
                           key=self.cache_access_times.get)
            self.response_cache.pop(oldest_key, None)
            self.cache_access_times.pop(oldest_key, None)
        
        self.response_cache[cache_key] = responses.copy()
        self.cache_access_times[cache_key] = time.time()
    
    def _update_performance_metrics(
        self,
        formatting_time: float,
        response_count: int
    ) -> None:
        """Update performance metrics"""
        
        # Update average formatting time
        self.avg_formatting_time = (
            (self.avg_formatting_time * (self.formatting_count - 1) + formatting_time) /
            self.formatting_count
        )
    
    def _initialize_code_highlighter(self):
        """Initialize code syntax highlighter"""
        # In a real implementation, this would initialize a syntax highlighter
        return None
    
    def _initialize_markdown_processor(self):
        """Initialize markdown processor"""
        # In a real implementation, this would initialize a markdown processor
        return None
    
    def _initialize_emoji_enhancer(self):
        """Initialize emoji enhancement system"""
        # In a real implementation, this would initialize emoji processing
        return None
    
    async def get_status(self) -> Dict[str, Any]:
        """Get response manager status and statistics"""
        
        cache_hit_rate = 0.0
        if self.formatting_count > 0:
            cache_hits = self.formatting_count - len(self.response_cache)
            cache_hit_rate = max(0.0, cache_hits / self.formatting_count)
        
        avg_readability = (
            sum(self.readability_metrics) / len(self.readability_metrics)
            if self.readability_metrics else 0.8
        )
        
        return {
            "formatting_count": self.formatting_count,
            "split_message_count": self.split_message_count,
            "media_processing_count": self.media_processing_count,
            "avg_formatting_time": self.avg_formatting_time,
            "cache_hit_rate": cache_hit_rate,
            "cache_size": len(self.response_cache),
            "avg_readability_score": avg_readability,
            "quality_enhancement": self.quality_enhancement,
            "smart_splitting": self.enable_smart_splitting
        }
    
    async def shutdown(self) -> None:
        """Gracefully shutdown the response manager"""
        logger.info("Shutting down response manager...")
        
        # Clear caches
        self.response_cache.clear()
        self.cache_access_times.clear()
        
        logger.info("Response manager shutdown complete")