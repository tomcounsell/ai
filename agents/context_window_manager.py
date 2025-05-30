#!/usr/bin/env python3
"""
Intelligent Context Window Manager

Manages conversation context efficiently for large conversations, optimizing
context window usage while preserving important information and maintaining
conversation continuity.

Key Features:
- Smart context truncation preserving critical information
- Conversation summarization for long histories
- Priority-based message retention
- Context size optimization for Claude Code integration
"""

import json
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum


class MessagePriority(Enum):
    """Priority levels for context retention."""
    CRITICAL = 1    # System messages, errors, important decisions
    HIGH = 2       # Recent messages, user questions, tool results
    MEDIUM = 3     # Regular conversation, context messages
    LOW = 4        # Old messages, routine responses


@dataclass
class ContextMetrics:
    """Metrics for context window management."""
    total_messages: int
    total_characters: int
    estimated_tokens: int
    retained_messages: int
    retained_characters: int
    compression_ratio: float
    processing_time_ms: float


class ContextWindowManager:
    """
    Intelligent context window management for unified agent system.
    
    Optimizes context usage while maintaining conversation quality and continuity.
    Designed for Claude Code integration with configurable limits.
    """
    
    def __init__(self, 
                 max_tokens: int = 100000,
                 max_messages: int = 200,
                 preserve_recent_count: int = 20,
                 enable_summarization: bool = True):
        """
        Initialize context window manager.
        
        Args:
            max_tokens: Maximum tokens to include in context
            max_messages: Maximum number of messages to retain
            preserve_recent_count: Always preserve this many recent messages
            enable_summarization: Whether to generate conversation summaries
        """
        self.max_tokens = max_tokens
        self.max_messages = max_messages
        self.preserve_recent_count = preserve_recent_count
        self.enable_summarization = enable_summarization
        
        # Rough token estimation (4 characters per token average)
        self.chars_per_token = 4
        self.max_characters = max_tokens * self.chars_per_token
        
        # Message priority patterns
        self.priority_patterns = {
            MessagePriority.CRITICAL: [
                r'\b(error|exception|failed|critical|important)\b',
                r'^(SYSTEM|ERROR|CRITICAL):',
                r'\b(claude code|development task|coding)\b'
            ],
            MessagePriority.HIGH: [
                r'\?$',  # Questions
                r'\b(search|create|generate|analyze|implement)\b',
                r'\b(notion|project|priority|task)\b',
                r'^(TOOL_RESULT|CONTEXT_DATA):'
            ],
            MessagePriority.MEDIUM: [
                r'\b(hello|hi|thanks|please)\b',
                r'\b(conversation|discuss|chat)\b'
            ]
        }
    
    def optimize_context(self, 
                        messages: List[Dict[str, Any]], 
                        context_data: Optional[Dict] = None) -> Tuple[List[Dict[str, Any]], ContextMetrics]:
        """
        Optimize conversation context for efficient processing.
        
        Args:
            messages: List of conversation messages
            context_data: Additional context information
            
        Returns:
            Tuple of (optimized_messages, metrics)
        """
        start_time = datetime.now()
        
        if not messages:
            return [], ContextMetrics(0, 0, 0, 0, 0, 1.0, 0)
        
        # Calculate initial metrics
        total_messages = len(messages)
        total_characters = sum(len(str(msg.get('content', ''))) for msg in messages)
        estimated_tokens = total_characters // self.chars_per_token
        
        # If under limits, return as-is
        if (estimated_tokens <= self.max_tokens and 
            total_messages <= self.max_messages):
            
            processing_time = (datetime.now() - start_time).total_seconds() * 1000
            metrics = ContextMetrics(
                total_messages=total_messages,
                total_characters=total_characters,
                estimated_tokens=estimated_tokens,
                retained_messages=total_messages,
                retained_characters=total_characters,
                compression_ratio=1.0,
                processing_time_ms=processing_time
            )
            return messages, metrics
        
        # Apply optimization strategies
        optimized_messages = self._apply_optimization_strategies(messages, context_data)
        
        # Calculate final metrics
        retained_messages = len(optimized_messages)
        retained_characters = sum(len(str(msg.get('content', ''))) for msg in optimized_messages)
        compression_ratio = retained_characters / total_characters if total_characters > 0 else 1.0
        
        processing_time = (datetime.now() - start_time).total_seconds() * 1000
        
        metrics = ContextMetrics(
            total_messages=total_messages,
            total_characters=total_characters,
            estimated_tokens=estimated_tokens,
            retained_messages=retained_messages,
            retained_characters=retained_characters,
            compression_ratio=compression_ratio,
            processing_time_ms=processing_time
        )
        
        return optimized_messages, metrics
    
    def _apply_optimization_strategies(self, 
                                     messages: List[Dict[str, Any]], 
                                     context_data: Optional[Dict] = None) -> List[Dict[str, Any]]:
        """Apply various optimization strategies to reduce context size."""
        
        # Strategy 1: Always preserve recent messages
        if len(messages) <= self.preserve_recent_count:
            return messages
        
        recent_messages = messages[-self.preserve_recent_count:]
        older_messages = messages[:-self.preserve_recent_count]
        
        # Strategy 2: Priority-based filtering for older messages
        prioritized_older = self._prioritize_messages(older_messages)
        
        # Strategy 3: Summarization of low-priority sections
        if self.enable_summarization and len(prioritized_older) > 50:
            summarized_older = self._summarize_low_priority_sections(prioritized_older)
        else:
            summarized_older = prioritized_older
        
        # Strategy 4: Final size-based truncation
        final_messages = self._apply_size_limits(summarized_older + recent_messages)
        
        return final_messages
    
    def _prioritize_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Assign priorities to messages and filter based on importance."""
        
        prioritized = []
        
        for msg in messages:
            content = str(msg.get('content', ''))
            priority = self._calculate_message_priority(content, msg)
            
            # Keep critical and high priority messages
            if priority in [MessagePriority.CRITICAL, MessagePriority.HIGH]:
                prioritized.append(msg)
            
            # Keep some medium priority messages (every 3rd)
            elif priority == MessagePriority.MEDIUM and len(prioritized) % 3 == 0:
                prioritized.append(msg)
            
            # Skip most low priority messages unless very recent
            elif priority == MessagePriority.LOW:
                # Keep 1 in 10 low priority messages for context
                if len(prioritized) % 10 == 0:
                    prioritized.append(msg)
        
        return prioritized
    
    def _calculate_message_priority(self, content: str, message: Dict[str, Any]) -> MessagePriority:
        """Calculate priority score for a message."""
        
        content_lower = content.lower()
        
        # Check for critical patterns
        for pattern in self.priority_patterns[MessagePriority.CRITICAL]:
            if re.search(pattern, content_lower, re.IGNORECASE):
                return MessagePriority.CRITICAL
        
        # Check for high priority patterns
        for pattern in self.priority_patterns[MessagePriority.HIGH]:
            if re.search(pattern, content_lower, re.IGNORECASE):
                return MessagePriority.HIGH
        
        # Check for medium priority patterns
        for pattern in self.priority_patterns[MessagePriority.MEDIUM]:
            if re.search(pattern, content_lower, re.IGNORECASE):
                return MessagePriority.MEDIUM
        
        # Additional priority factors
        role = message.get('role', '')
        
        # System and assistant messages often contain important info
        if role == 'system':
            return MessagePriority.CRITICAL
        elif role == 'assistant' and len(content) > 200:
            return MessagePriority.HIGH
        
        # Long user messages might be important
        if role == 'user' and len(content) > 100:
            return MessagePriority.MEDIUM
        
        return MessagePriority.LOW
    
    def _summarize_low_priority_sections(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Summarize consecutive low-priority messages to save space."""
        
        result = []
        current_low_priority_batch = []
        
        for msg in messages:
            content = str(msg.get('content', ''))
            priority = self._calculate_message_priority(content, msg)
            
            if priority == MessagePriority.LOW:
                current_low_priority_batch.append(msg)
            else:
                # Process any accumulated low priority messages
                if current_low_priority_batch:
                    summary = self._create_batch_summary(current_low_priority_batch)
                    if summary:
                        result.append(summary)
                    current_low_priority_batch = []
                
                # Add the non-low-priority message
                result.append(msg)
        
        # Handle any remaining low priority batch
        if current_low_priority_batch:
            summary = self._create_batch_summary(current_low_priority_batch)
            if summary:
                result.append(summary)
        
        return result
    
    def _create_batch_summary(self, messages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Create a summary message for a batch of low-priority messages."""
        
        if len(messages) < 3:  # Don't summarize very small batches
            return None
        
        # Extract key information
        user_messages = [msg for msg in messages if msg.get('role') == 'user']
        assistant_messages = [msg for msg in messages if msg.get('role') == 'assistant']
        
        # Create concise summary
        summary_parts = []
        
        if user_messages:
            user_count = len(user_messages)
            summary_parts.append(f"{user_count} user messages")
        
        if assistant_messages:
            assistant_count = len(assistant_messages)
            summary_parts.append(f"{assistant_count} assistant responses")
        
        # Include time range if available
        first_msg = messages[0]
        last_msg = messages[-1]
        
        summary_content = f"[SUMMARY: {', '.join(summary_parts)} in conversation segment]"
        
        return {
            'role': 'system',
            'content': summary_content,
            'timestamp': last_msg.get('timestamp', ''),
            'is_summary': True,
            'original_count': len(messages)
        }
    
    def _apply_size_limits(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Apply final size limits to ensure context fits within constraints."""
        
        if not messages:
            return messages
        
        # Calculate current size
        current_chars = sum(len(str(msg.get('content', ''))) for msg in messages)
        current_tokens = current_chars // self.chars_per_token
        
        # If under limits, return as-is
        if current_tokens <= self.max_tokens and len(messages) <= self.max_messages:
            return messages
        
        # Preserve recent messages and trim from the beginning
        result = []
        remaining_chars = self.max_characters
        remaining_messages = self.max_messages
        
        # Start from the end (most recent) and work backwards
        for msg in reversed(messages):
            content = str(msg.get('content', ''))
            msg_chars = len(content)
            
            if (msg_chars <= remaining_chars and 
                len(result) < remaining_messages):
                
                result.insert(0, msg)
                remaining_chars -= msg_chars
            else:
                # If we can't fit the whole message but have lots of space left,
                # try to truncate it
                if remaining_chars > 500 and len(result) < remaining_messages:
                    truncated_content = content[:remaining_chars-100] + "... [truncated]"
                    truncated_msg = msg.copy()
                    truncated_msg['content'] = truncated_content
                    truncated_msg['is_truncated'] = True
                    result.insert(0, truncated_msg)
                    break
                else:
                    break
        
        return result
    
    def get_context_summary(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Generate a summary of the conversation context."""
        
        if not messages:
            return {"summary": "No conversation history", "key_points": []}
        
        # Count message types
        user_messages = [msg for msg in messages if msg.get('role') == 'user']
        assistant_messages = [msg for msg in messages if msg.get('role') == 'assistant']
        system_messages = [msg for msg in messages if msg.get('role') == 'system']
        
        # Extract recent topics
        recent_messages = messages[-10:] if len(messages) > 10 else messages
        topics = self._extract_conversation_topics(recent_messages)
        
        # Identify key themes
        all_content = ' '.join(str(msg.get('content', '')) for msg in messages)
        themes = self._identify_themes(all_content)
        
        return {
            "summary": f"Conversation with {len(user_messages)} user messages and {len(assistant_messages)} responses",
            "message_counts": {
                "user": len(user_messages),
                "assistant": len(assistant_messages),
                "system": len(system_messages),
                "total": len(messages)
            },
            "recent_topics": topics,
            "key_themes": themes,
            "conversation_length": len(all_content),
            "estimated_tokens": len(all_content) // self.chars_per_token
        }
    
    def _extract_conversation_topics(self, messages: List[Dict[str, Any]]) -> List[str]:
        """Extract main topics from recent conversation."""
        
        topics = []
        
        for msg in messages:
            content = str(msg.get('content', '')).lower()
            
            # Look for topic indicators
            if any(word in content for word in ['about', 'regarding', 'discuss', 'question']):
                # Extract potential topic (simple heuristic)
                words = content.split()
                for i, word in enumerate(words):
                    if word in ['about', 'regarding'] and i + 1 < len(words):
                        topic = words[i + 1]
                        if len(topic) > 3 and topic not in topics:
                            topics.append(topic)
        
        return topics[:5]  # Return up to 5 topics
    
    def _identify_themes(self, content: str) -> List[str]:
        """Identify key themes in the conversation."""
        
        content_lower = content.lower()
        themes = []
        
        # Technical themes
        if any(word in content_lower for word in ['code', 'programming', 'development', 'claude code']):
            themes.append('development')
        
        if any(word in content_lower for word in ['notion', 'project', 'task', 'priority']):
            themes.append('project_management')
        
        if any(word in content_lower for word in ['search', 'find', 'information', 'news']):
            themes.append('information_seeking')
        
        if any(word in content_lower for word in ['image', 'create', 'generate', 'picture']):
            themes.append('creative')
        
        if any(word in content_lower for word in ['help', 'support', 'question', 'problem']):
            themes.append('assistance')
        
        return themes
    
    def validate_context_health(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Validate the health and quality of the conversation context."""
        
        health_report = {
            "status": "healthy",
            "warnings": [],
            "recommendations": [],
            "metrics": {}
        }
        
        if not messages:
            health_report["status"] = "empty"
            health_report["warnings"].append("No conversation history")
            return health_report
        
        # Check for excessive size
        total_chars = sum(len(str(msg.get('content', ''))) for msg in messages)
        estimated_tokens = total_chars // self.chars_per_token
        
        health_report["metrics"] = {
            "message_count": len(messages),
            "character_count": total_chars,
            "estimated_tokens": estimated_tokens,
            "token_utilization": (estimated_tokens / self.max_tokens) * 100
        }
        
        # Size warnings
        if estimated_tokens > self.max_tokens * 0.9:
            health_report["warnings"].append("Context approaching token limit")
            health_report["recommendations"].append("Consider context optimization")
        
        if len(messages) > self.max_messages * 0.9:
            health_report["warnings"].append("Context approaching message limit")
            health_report["recommendations"].append("Consider message summarization")
        
        # Quality checks
        recent_messages = messages[-10:] if len(messages) > 10 else messages
        user_to_assistant_ratio = self._calculate_user_assistant_ratio(recent_messages)
        
        if user_to_assistant_ratio > 3:
            health_report["warnings"].append("Too many consecutive user messages")
            health_report["recommendations"].append("Check for missed assistant responses")
        
        # Check for repetitive content
        if self._detect_repetitive_content(recent_messages):
            health_report["warnings"].append("Repetitive content detected")
            health_report["recommendations"].append("Consider conversation reset")
        
        # Overall health status
        if health_report["warnings"]:
            health_report["status"] = "needs_attention" if len(health_report["warnings"]) > 2 else "warning"
        
        return health_report
    
    def _calculate_user_assistant_ratio(self, messages: List[Dict[str, Any]]) -> float:
        """Calculate ratio of user to assistant messages."""
        user_count = sum(1 for msg in messages if msg.get('role') == 'user')
        assistant_count = sum(1 for msg in messages if msg.get('role') == 'assistant')
        
        if assistant_count == 0:
            return float('inf') if user_count > 0 else 0
        
        return user_count / assistant_count
    
    def _detect_repetitive_content(self, messages: List[Dict[str, Any]]) -> bool:
        """Detect if there's repetitive content in recent messages."""
        
        if len(messages) < 4:
            return False
        
        contents = [str(msg.get('content', '')) for msg in messages[-6:]]
        
        # Simple repetition detection
        for i in range(len(contents) - 1):
            for j in range(i + 1, len(contents)):
                # Check for very similar content
                similarity = self._calculate_similarity(contents[i], contents[j])
                if similarity > 0.8:  # 80% similarity threshold
                    return True
        
        return False
    
    def _calculate_similarity(self, text1: str, text2: str) -> float:
        """Calculate simple similarity between two texts."""
        
        if not text1 or not text2:
            return 0.0
        
        # Simple word-based similarity
        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())
        
        if not words1 and not words2:
            return 1.0
        
        intersection = words1.intersection(words2)
        union = words1.union(words2)
        
        return len(intersection) / len(union) if union else 0.0