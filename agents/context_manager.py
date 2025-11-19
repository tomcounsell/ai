"""Context Window Manager

This module provides comprehensive context window management for AI agents,
including token counting, context compression, and intelligent message
preservation strategies.
"""

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Import MessageEntry directly as it's just a data class
from .valor.context import MessageEntry

# Import ValorContext via TYPE_CHECKING to avoid circular imports
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .valor.context import ValorContext


class CompressionStrategy(BaseModel):
    """Configuration for context compression strategies."""
    
    preserve_recent: int = 20
    preserve_important_threshold: float = 7.0
    preserve_system_messages: bool = True
    preserve_tool_results: bool = True
    max_summary_length: int = 500
    compression_ratio_target: float = 0.3


class TokenEstimator:
    """Utility class for estimating token counts."""
    
    # Rough estimates for different model families
    TOKEN_RATIOS = {
        'gpt-4': 0.75,      # characters to tokens ratio
        'gpt-3.5': 0.75,
        'claude': 0.8,
        'gemini': 0.7,
        'default': 0.75
    }
    
    @classmethod
    def estimate_tokens(cls, text: str, model_family: str = 'default') -> int:
        """
        Estimate token count for given text.
        
        Args:
            text: Text to estimate tokens for
            model_family: Model family for more accurate estimation
            
        Returns:
            int: Estimated token count
        """
        if not text:
            return 0
        
        # Basic character-based estimation
        char_count = len(text)
        ratio = cls.TOKEN_RATIOS.get(model_family, cls.TOKEN_RATIOS['default'])
        
        # Adjust for common patterns
        # Words are typically 1-2 tokens
        word_count = len(text.split())
        
        # Use the more conservative estimate
        char_estimate = int(char_count * ratio)
        word_estimate = int(word_count * 1.3)  # Average 1.3 tokens per word
        
        return max(char_estimate, word_estimate)
    
    @classmethod
    def estimate_message_tokens(cls, message: MessageEntry, model_family: str = 'default') -> int:
        """
        Estimate tokens for a complete message including metadata.
        
        Args:
            message: Message to estimate tokens for
            model_family: Model family for estimation
            
        Returns:
            int: Estimated token count
        """
        if message.token_count is not None:
            return message.token_count
        
        # Content tokens
        content_tokens = cls.estimate_tokens(message.content, model_family)
        
        # Role and metadata overhead (typically 3-5 tokens)
        overhead_tokens = 5
        
        # Metadata tokens if present
        metadata_tokens = 0
        if message.metadata:
            metadata_text = str(message.metadata)
            metadata_tokens = cls.estimate_tokens(metadata_text, model_family)
        
        total_tokens = content_tokens + overhead_tokens + metadata_tokens
        
        # Cache the result
        message.token_count = total_tokens
        
        return total_tokens


class ContextCompressor:
    """Handles different context compression strategies."""
    
    def __init__(self, strategy: CompressionStrategy):
        self.strategy = strategy
    
    async def compress_messages(
        self,
        messages: List[MessageEntry],
        target_tokens: int,
        model_family: str = 'default'
    ) -> Tuple[List[MessageEntry], str]:
        """
        Compress message list to fit within target token count.
        
        Args:
            messages: List of messages to compress
            target_tokens: Target token count after compression
            model_family: Model family for token estimation
            
        Returns:
            Tuple[List[MessageEntry], str]: (compressed_messages, compression_summary)
        """
        if not messages:
            return [], "No messages to compress"
        
        # Calculate current token usage
        current_tokens = sum(
            TokenEstimator.estimate_message_tokens(msg, model_family) 
            for msg in messages
        )
        
        if current_tokens <= target_tokens:
            return messages, f"No compression needed ({current_tokens} <= {target_tokens} tokens)"
        
        # Apply compression strategy
        preserved_messages = self._select_messages_for_preservation(messages, model_family)
        
        # If still too many tokens, create summary
        preserved_tokens = sum(
            TokenEstimator.estimate_message_tokens(msg, model_family) 
            for msg in preserved_messages
        )
        
        if preserved_tokens > target_tokens:
            # Create summary of removed messages
            removed_messages = [msg for msg in messages if msg not in preserved_messages]
            summary = await self._create_conversation_summary(removed_messages)
            
            # Create summary message
            summary_message = MessageEntry(
                role="system",
                content=f"[Context Summary] Previous conversation summary: {summary}",
                importance_score=9.0,
                token_count=TokenEstimator.estimate_tokens(summary, model_family)
            )
            
            # Include summary with most recent messages
            final_messages = [summary_message] + preserved_messages[-self.strategy.preserve_recent:]
        else:
            final_messages = preserved_messages
        
        compression_summary = (
            f"Compressed from {len(messages)} to {len(final_messages)} messages "
            f"({current_tokens} to ~{sum(TokenEstimator.estimate_message_tokens(msg, model_family) for msg in final_messages)} tokens)"
        )
        
        return final_messages, compression_summary
    
    def _select_messages_for_preservation(
        self,
        messages: List[MessageEntry],
        model_family: str
    ) -> List[MessageEntry]:
        """Select which messages to preserve during compression."""
        preserved = set()
        
        # Always preserve recent messages
        recent_messages = messages[-self.strategy.preserve_recent:]
        preserved.update(msg.id for msg in recent_messages)
        
        # Preserve important messages
        for msg in messages:
            if msg.importance_score >= self.strategy.preserve_important_threshold:
                preserved.add(msg.id)
        
        # Preserve system messages if configured
        if self.strategy.preserve_system_messages:
            for msg in messages:
                if msg.role == "system":
                    preserved.add(msg.id)
        
        # Preserve tool results if configured
        if self.strategy.preserve_tool_results:
            for msg in messages:
                if msg.role == "tool" or "tool_result" in msg.metadata:
                    preserved.add(msg.id)
        
        # Return messages in chronological order
        return [msg for msg in messages if msg.id in preserved]
    
    async def _create_conversation_summary(self, messages: List[MessageEntry]) -> str:
        """Create a summary of conversation messages."""
        if not messages:
            return "No previous conversation."
        
        # Simple summarization - in production, this could use an LLM
        summary_points = []
        
        # Group messages by type
        user_messages = [msg for msg in messages if msg.role == "user"]
        assistant_messages = [msg for msg in messages if msg.role == "assistant"]
        
        if user_messages:
            # Extract key topics from user messages
            user_topics = self._extract_topics(user_messages)
            if user_topics:
                summary_points.append(f"User discussed: {', '.join(user_topics)}")
        
        if assistant_messages:
            # Extract key responses from assistant
            assistant_topics = self._extract_topics(assistant_messages)
            if assistant_topics:
                summary_points.append(f"Assistant provided help with: {', '.join(assistant_topics)}")
        
        # Add tool usage information
        tool_messages = [msg for msg in messages if msg.role == "tool"]
        if tool_messages:
            tools_used = set(msg.metadata.get('tool_name', 'unknown') for msg in tool_messages)
            summary_points.append(f"Tools used: {', '.join(tools_used)}")
        
        # Combine summary points
        summary = ". ".join(summary_points) if summary_points else "General conversation"
        
        # Truncate if too long
        if len(summary) > self.strategy.max_summary_length:
            summary = summary[:self.strategy.max_summary_length - 3] + "..."
        
        return summary
    
    def _extract_topics(self, messages: List[MessageEntry]) -> List[str]:
        """Extract key topics from messages (simple keyword-based approach)."""
        # This is a simplified topic extraction - in production, use NLP libraries
        text = " ".join(msg.content for msg in messages).lower()
        
        # Common technical topics
        topics = []
        topic_patterns = {
            'code': r'\b(code|programming|function|class|variable|debug)\b',
            'database': r'\b(database|sql|query|table|schema)\b',
            'api': r'\b(api|endpoint|request|response|http)\b',
            'deployment': r'\b(deploy|deployment|server|hosting|production)\b',
            'testing': r'\b(test|testing|unit test|integration)\b',
            'documentation': r'\b(document|docs|readme|guide)\b',
            'configuration': r'\b(config|configuration|settings|setup)\b',
        }
        
        for topic, pattern in topic_patterns.items():
            if re.search(pattern, text):
                topics.append(topic)
        
        return topics[:5]  # Limit to 5 topics


class ContextWindowManager:
    """
    Comprehensive context window management for AI agents.
    
    Handles token counting, context compression, and intelligent
    preservation of important messages within token limits.
    """
    
    def __init__(
        self,
        max_tokens: int = 100_000,
        compression_strategy: Optional[CompressionStrategy] = None,
        model_family: str = 'default'
    ):
        """
        Initialize the context window manager.
        
        Args:
            max_tokens: Maximum tokens for context window
            compression_strategy: Strategy for context compression
            model_family: Model family for token estimation
        """
        self.max_tokens = max_tokens
        self.model_family = model_family
        self.compression_strategy = compression_strategy or CompressionStrategy()
        self.compressor = ContextCompressor(self.compression_strategy)
        
        logger.info(f"Context window manager initialized with {max_tokens} max tokens")
    
    def count_tokens(self, context: "ValorContext") -> int:
        """
        Count total tokens in a context.
        
        Args:
            context: "ValorContext" to count tokens for
            
        Returns:
            int: Total estimated token count
        """
        total_tokens = 0
        
        # Count message tokens
        for message in context.message_history:
            total_tokens += TokenEstimator.estimate_message_tokens(message, self.model_family)
        
        # Count context metadata tokens (small overhead)
        metadata_size = len(str(context.session_metadata)) + len(context.workspace)
        total_tokens += TokenEstimator.estimate_tokens(str(metadata_size), self.model_family)
        
        # Update metrics
        context.context_metrics.total_tokens = total_tokens
        
        return total_tokens
    
    def needs_compression(self, context: "ValorContext", threshold: float = 0.85) -> bool:
        """
        Check if context needs compression.
        
        Args:
            context: "ValorContext" to check
            threshold: Threshold ratio (0.0-1.0) for triggering compression
            
        Returns:
            bool: True if compression is needed
        """
        current_tokens = self.count_tokens(context)
        return current_tokens > (self.max_tokens * threshold)
    
    async def compress_context(self, context: "ValorContext") -> "ValorContext":
        """
        Compress context to fit within token limits.
        
        Args:
            context: "ValorContext" to compress
            
        Returns:
            ValorContext: Context with compressed message history
        """
        if not context.message_history:
            return context
        
        # Calculate target tokens (leave room for new messages)
        target_tokens = int(self.max_tokens * self.compression_strategy.compression_ratio_target)
        
        # Compress message history
        compressed_messages, summary = await self.compressor.compress_messages(
            context.message_history,
            target_tokens,
            self.model_family
        )
        
        # Update context
        removed_count = len(context.message_history) - len(compressed_messages)
        context.message_history = compressed_messages
        
        # Update summary if compression occurred
        if removed_count > 0:
            context.context_summary = summary
            context.context_metrics.context_compressions += 1
            context.context_metrics.last_compression = datetime.now(timezone.utc)
        
        logger.info(f"Context compressed: removed {removed_count} messages")
        
        return context
    
    async def get_conversation_summary(self, context: "ValorContext") -> str:
        """
        Get a summary of the conversation in the context.
        
        Args:
            context: "ValorContext" to summarize
            
        Returns:
            str: Conversation summary
        """
        if context.context_summary:
            return context.context_summary
        
        # Create summary from message history
        summary = await self.compressor._create_conversation_summary(context.message_history)
        return summary
    
    def get_context_stats(self, context: "ValorContext") -> Dict[str, Any]:
        """
        Get detailed statistics about context usage.
        
        Args:
            context: "ValorContext" to analyze
            
        Returns:
            Dict[str, Any]: Context statistics
        """
        total_tokens = self.count_tokens(context)
        
        # Analyze message distribution
        role_distribution = {}
        token_distribution = {}
        
        for message in context.message_history:
            role = message.role
            msg_tokens = TokenEstimator.estimate_message_tokens(message, self.model_family)
            
            role_distribution[role] = role_distribution.get(role, 0) + 1
            token_distribution[role] = token_distribution.get(role, 0) + msg_tokens
        
        # Calculate utilization
        utilization = total_tokens / self.max_tokens if self.max_tokens > 0 else 0
        
        return {
            'total_tokens': total_tokens,
            'max_tokens': self.max_tokens,
            'utilization': utilization,
            'needs_compression': self.needs_compression(context),
            'message_count': len(context.message_history),
            'role_distribution': role_distribution,
            'token_distribution': token_distribution,
            'important_messages': len(context.important_messages),
            'compression_count': context.context_metrics.context_compressions,
            'last_compression': (
                context.context_metrics.last_compression.isoformat()
                if context.context_metrics.last_compression else None
            )
        }
    
    def optimize_context(self, context: "ValorContext") -> "ValorContext":
        """
        Optimize context by removing duplicates and updating token counts.
        
        Args:
            context: "ValorContext" to optimize
            
        Returns:
            ValorContext: Optimized context
        """
        if not context.message_history:
            return context
        
        # Remove duplicate messages (by content and timestamp)
        seen = set()
        unique_messages = []
        
        for message in context.message_history:
            # Create a simple hash of content and timestamp
            msg_hash = hash((message.content, message.timestamp.isoformat(), message.role))
            
            if msg_hash not in seen:
                seen.add(msg_hash)
                # Update token count if not set
                if message.token_count is None:
                    message.token_count = TokenEstimator.estimate_message_tokens(
                        message, self.model_family
                    )
                unique_messages.append(message)
        
        # Update context
        removed_duplicates = len(context.message_history) - len(unique_messages)
        context.message_history = unique_messages
        
        if removed_duplicates > 0:
            logger.debug(f"Removed {removed_duplicates} duplicate messages")
        
        return context
    
    def set_compression_strategy(self, strategy: CompressionStrategy) -> None:
        """Update the compression strategy."""
        self.compression_strategy = strategy
        self.compressor = ContextCompressor(strategy)
        logger.info("Updated compression strategy")
    
    def estimate_tokens_for_text(self, text: str) -> int:
        """
        Estimate tokens for arbitrary text.
        
        Args:
            text: Text to estimate tokens for
            
        Returns:
            int: Estimated token count
        """
        return TokenEstimator.estimate_tokens(text, self.model_family)
    
    async def prepare_context_for_inference(
        self,
        context: "ValorContext",
        additional_tokens: int = 1000
    ) -> Tuple["ValorContext", Dict[str, Any]]:
        """
        Prepare context for model inference by ensuring token limits.
        
        Args:
            context: Context to prepare
            additional_tokens: Tokens to reserve for model response
            
        Returns:
            Tuple["ValorContext", Dict[str, Any]]: (prepared_context, preparation_info)
        """
        preparation_info = {
            'original_message_count': len(context.message_history),
            'original_token_count': self.count_tokens(context),
            'compression_performed': False,
            'optimization_performed': False
        }
        
        # Optimize first
        optimized_context = self.optimize_context(context)
        if len(optimized_context.message_history) < preparation_info['original_message_count']:
            preparation_info['optimization_performed'] = True
        
        # Check if compression is needed
        available_tokens = self.max_tokens - additional_tokens
        current_tokens = self.count_tokens(optimized_context)
        
        if current_tokens > available_tokens:
            # Compress context
            compressed_context = await self.compress_context(optimized_context)
            preparation_info['compression_performed'] = True
            preparation_info['final_message_count'] = len(compressed_context.message_history)
            preparation_info['final_token_count'] = self.count_tokens(compressed_context)
            
            return compressed_context, preparation_info
        
        preparation_info['final_message_count'] = len(optimized_context.message_history)
        preparation_info['final_token_count'] = current_tokens
        
        return optimized_context, preparation_info