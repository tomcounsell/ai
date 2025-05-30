#!/usr/bin/env python3
"""
Streaming Performance Optimizer

Manages streaming update rates and optimization for the unified Valor-Claude system,
ensuring consistent 2-3 second update intervals during development tasks while
optimizing for different content types and network conditions.

Key Features:
- Adaptive rate control based on content size and complexity
- Network condition awareness and adjustment
- Development task streaming optimization
- Content-aware update frequency tuning
"""

import asyncio
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, timedelta


class ContentType(Enum):
    """Content types for streaming optimization."""
    TEXT_SHORT = 1      # Simple text responses < 500 chars
    TEXT_LONG = 2       # Long text responses > 500 chars
    CODE_SNIPPET = 3    # Code blocks and technical content
    DEVELOPMENT_TASK = 4  # Claude Code development tasks
    STREAMING_OUTPUT = 5  # Real-time streaming content
    ERROR_MESSAGE = 6   # Error responses (high priority)


class NetworkCondition(Enum):
    """Network condition states for optimization."""
    EXCELLENT = 1  # <100ms latency, stable
    GOOD = 2       # 100-300ms latency
    FAIR = 3       # 300-500ms latency  
    POOR = 4       # >500ms latency or unstable


@dataclass
class StreamingMetrics:
    """Metrics for streaming performance analysis."""
    total_updates: int
    average_interval: float
    target_compliance_rate: float  # % of updates within target range
    content_type_distribution: Dict[ContentType, int]
    network_adaptations: int
    optimization_score: float


@dataclass
class UpdateTiming:
    """Timing information for a streaming update."""
    content_size: int
    content_type: ContentType
    estimated_read_time: float
    network_condition: NetworkCondition
    previous_interval: float
    recommended_interval: float


class StreamingOptimizer:
    """
    Intelligent streaming performance optimizer for unified agent system.
    
    Optimizes update frequency based on content characteristics, user reading
    patterns, and network conditions to maintain consistent 2-3 second intervals.
    """
    
    def __init__(self, 
                 target_interval: float = 2.5,
                 min_interval: float = 1.0,
                 max_interval: float = 5.0,
                 adaptation_factor: float = 0.2):
        """
        Initialize streaming optimizer.
        
        Args:
            target_interval: Target streaming interval in seconds
            min_interval: Minimum allowed interval
            max_interval: Maximum allowed interval
            adaptation_factor: Rate of adaptation (0.0 - 1.0)
        """
        self.target_interval = target_interval
        self.min_interval = min_interval
        self.max_interval = max_interval
        self.adaptation_factor = adaptation_factor
        
        # Performance tracking
        self.update_history: List[UpdateTiming] = []
        self.network_latency_history: List[float] = []
        self.last_update_time = time.time()
        
        # Content type configurations - optimized for 2-3s target range
        self.content_type_configs = {
            ContentType.TEXT_SHORT: {
                "base_interval": 2.0,  # Increased to hit target range
                "read_time_multiplier": 0.8,
                "priority": 1
            },
            ContentType.TEXT_LONG: {
                "base_interval": 2.8,  # Adjusted to stay in range
                "read_time_multiplier": 1.0,  # Reduced multiplier
                "priority": 2
            },
            ContentType.CODE_SNIPPET: {
                "base_interval": 2.3,  # Optimized for target
                "read_time_multiplier": 1.2,  # Reduced multiplier
                "priority": 2
            },
            ContentType.DEVELOPMENT_TASK: {
                "base_interval": 2.5,  # Already optimal
                "read_time_multiplier": 1.0,
                "priority": 3
            },
            ContentType.STREAMING_OUTPUT: {
                "base_interval": 2.2,  # Increased for target range
                "read_time_multiplier": 0.9,
                "priority": 1
            },
            ContentType.ERROR_MESSAGE: {
                "base_interval": 1.5,  # Slightly increased while keeping urgency
                "read_time_multiplier": 0.7,  # Adjusted
                "priority": 0  # Highest priority
            }
        }
        
        # Network condition adjustments
        self.network_adjustments = {
            NetworkCondition.EXCELLENT: 1.0,
            NetworkCondition.GOOD: 1.1,
            NetworkCondition.FAIR: 1.3,
            NetworkCondition.POOR: 1.5
        }
    
    def calculate_optimal_interval(self, 
                                 content: str, 
                                 content_type: ContentType,
                                 context: Optional[Dict] = None) -> UpdateTiming:
        """
        Calculate optimal streaming interval for given content.
        
        Args:
            content: Content to be streamed
            content_type: Type of content for optimization
            context: Additional context for optimization
            
        Returns:
            UpdateTiming with recommended interval and analysis
        """
        current_time = time.time()
        previous_interval = current_time - self.last_update_time
        
        # Analyze content characteristics
        content_size = len(content)
        estimated_read_time = self._estimate_reading_time(content, content_type)
        network_condition = self._assess_network_condition()
        
        # Get base configuration
        config = self.content_type_configs[content_type]
        base_interval = config["base_interval"]
        
        # Apply content-based adjustments
        size_adjustment = self._calculate_size_adjustment(content_size, content_type)
        read_time_adjustment = estimated_read_time * config["read_time_multiplier"]
        network_adjustment = self.network_adjustments[network_condition]
        
        # Calculate recommended interval
        recommended_interval = base_interval * size_adjustment * network_adjustment
        
        # Apply adaptation based on recent performance
        if len(self.update_history) > 5:
            adaptation_adjustment = self._calculate_adaptation_adjustment()
            recommended_interval *= adaptation_adjustment
        
        # Apply bounds
        recommended_interval = max(self.min_interval, 
                                 min(self.max_interval, recommended_interval))
        
        # Create timing object
        timing = UpdateTiming(
            content_size=content_size,
            content_type=content_type,
            estimated_read_time=estimated_read_time,
            network_condition=network_condition,
            previous_interval=previous_interval,
            recommended_interval=recommended_interval
        )
        
        # Record for learning
        self.update_history.append(timing)
        self._maintain_history_size()
        
        return timing
    
    def _estimate_reading_time(self, content: str, content_type: ContentType) -> float:
        """Estimate time needed to read/process content."""
        content_size = len(content)
        
        if content_type == ContentType.TEXT_SHORT:
            # ~250 words per minute, ~5 chars per word
            return (content_size / 5) / 250 * 60
        elif content_type == ContentType.TEXT_LONG:
            # Slower reading for long content
            return (content_size / 5) / 200 * 60
        elif content_type == ContentType.CODE_SNIPPET:
            # Code takes longer to read and understand
            return (content_size / 5) / 150 * 60
        elif content_type == ContentType.DEVELOPMENT_TASK:
            # Development tasks need processing time
            return max(2.0, (content_size / 5) / 200 * 60)
        elif content_type == ContentType.STREAMING_OUTPUT:
            # Streaming content is consumed in real-time
            return content_size / 1000  # ~1 second per 1000 chars
        elif content_type == ContentType.ERROR_MESSAGE:
            # Errors should be read quickly
            return min(1.0, (content_size / 5) / 300 * 60)
        
        return 2.0  # Default fallback
    
    def _calculate_size_adjustment(self, content_size: int, content_type: ContentType) -> float:
        """Calculate adjustment factor based on content size - optimized for 2-3s target."""
        if content_type == ContentType.DEVELOPMENT_TASK:
            # Development tasks scale differently but stay in target range
            if content_size < 500:
                return 0.9  # Slightly reduced to stay above 2s
            elif content_size < 2000:
                return 1.0  # Standard interval
            else:
                return 1.1  # Moderate increase to stay under 3s
        
        # General size adjustments - more conservative to hit target range
        if content_size < 100:
            return 0.85  # Less aggressive reduction
        elif content_size < 500:
            return 0.95  # Closer to base
        elif content_size < 1500:
            return 1.0
        elif content_size < 3000:
            return 1.05  # Smaller increase
        else:
            return 1.1   # Cap increase to stay in range
    
    def _assess_network_condition(self) -> NetworkCondition:
        """Assess current network conditions based on recent latency."""
        if len(self.network_latency_history) < 3:
            return NetworkCondition.GOOD  # Default assumption
        
        recent_latency = sum(self.network_latency_history[-5:]) / min(5, len(self.network_latency_history))
        
        if recent_latency < 0.1:
            return NetworkCondition.EXCELLENT
        elif recent_latency < 0.3:
            return NetworkCondition.GOOD
        elif recent_latency < 0.5:
            return NetworkCondition.FAIR
        else:
            return NetworkCondition.POOR
    
    def _calculate_adaptation_adjustment(self) -> float:
        """Calculate adjustment based on recent performance vs targets."""
        recent_updates = self.update_history[-10:]
        intervals = [update.recommended_interval for update in recent_updates]
        
        if not intervals:
            return 1.0
        
        avg_interval = sum(intervals) / len(intervals)
        target_diff = avg_interval - self.target_interval
        
        # If we're consistently off target, adjust
        if abs(target_diff) > 0.5:
            # Move towards target by adaptation factor
            adjustment = 1.0 - (target_diff / self.target_interval * self.adaptation_factor)
            return max(0.5, min(2.0, adjustment))
        
        return 1.0
    
    def _maintain_history_size(self, max_history: int = 100):
        """Maintain reasonable history size for performance."""
        if len(self.update_history) > max_history:
            self.update_history = self.update_history[-max_history:]
        
        if len(self.network_latency_history) > max_history:
            self.network_latency_history = self.network_latency_history[-max_history:]
    
    def record_network_latency(self, latency: float):
        """Record network latency measurement for optimization."""
        self.network_latency_history.append(latency)
        self._maintain_history_size()
    
    def classify_content_type(self, content: str, context: Optional[Dict] = None) -> ContentType:
        """
        Classify content type for optimization.
        
        Args:
            content: Content to classify
            context: Additional context (chat_id, username, etc.)
            
        Returns:
            ContentType classification
        """
        content_lower = content.lower()
        content_size = len(content)
        
        # Check for error patterns
        if any(pattern in content_lower for pattern in ['error', 'failed', 'exception', '❌']):
            return ContentType.ERROR_MESSAGE
        
        # Check for development task patterns
        if any(pattern in content_lower for pattern in [
            'claude code', 'development task', 'implement', 'coding', 'debug'
        ]):
            return ContentType.DEVELOPMENT_TASK
        
        # Check for code patterns
        if any(pattern in content for pattern in [
            '```', 'def ', 'class ', 'import ', 'function', 'const ', 'let ', 'var '
        ]):
            return ContentType.CODE_SNIPPET
        
        # Check for streaming patterns
        if context and context.get('is_streaming', False):
            return ContentType.STREAMING_OUTPUT
        
        # Size-based classification for text
        if content_size < 500:
            return ContentType.TEXT_SHORT
        else:
            return ContentType.TEXT_LONG
    
    def optimize_streaming_rate(self, content: str, context: Optional[Dict] = None) -> float:
        """
        Primary method: Calculate optimal streaming rate for content.
        
        Args:
            content: Content being streamed
            context: Additional context for optimization
            
        Returns:
            Optimal interval in seconds
        """
        content_type = self.classify_content_type(content, context)
        timing = self.calculate_optimal_interval(content, content_type, context)
        
        # Update last update time
        self.last_update_time = time.time()
        
        return timing.recommended_interval
    
    def get_performance_metrics(self) -> StreamingMetrics:
        """Get comprehensive streaming performance metrics."""
        if not self.update_history:
            return StreamingMetrics(0, 0, 0, {}, 0, 0)
        
        # Calculate metrics
        total_updates = len(self.update_history)
        intervals = [update.recommended_interval for update in self.update_history]
        average_interval = sum(intervals) / len(intervals)
        
        # Target compliance (within 2-3 second range)
        target_compliant = len([i for i in intervals if 2.0 <= i <= 3.0])
        target_compliance_rate = (target_compliant / len(intervals)) * 100
        
        # Content type distribution
        content_distribution = {}
        for content_type in ContentType:
            count = len([u for u in self.update_history if u.content_type == content_type])
            content_distribution[content_type] = count
        
        # Network adaptations
        network_adaptations = len([u for u in self.update_history 
                                 if u.network_condition != NetworkCondition.GOOD])
        
        # Calculate optimization score (0-100)
        optimization_score = self._calculate_optimization_score(
            target_compliance_rate, average_interval
        )
        
        return StreamingMetrics(
            total_updates=total_updates,
            average_interval=average_interval,
            target_compliance_rate=target_compliance_rate,
            content_type_distribution=content_distribution,
            network_adaptations=network_adaptations,
            optimization_score=optimization_score
        )
    
    def _calculate_optimization_score(self, compliance_rate: float, avg_interval: float) -> float:
        """Calculate overall optimization score (0-100)."""
        # Score based on target compliance
        compliance_score = min(100, compliance_rate)
        
        # Score based on average interval proximity to target
        interval_diff = abs(avg_interval - self.target_interval)
        interval_score = max(0, 100 - (interval_diff / self.target_interval * 100))
        
        # Weighted average
        return (compliance_score * 0.7) + (interval_score * 0.3)
    
    def validate_performance_targets(self) -> Dict[str, bool]:
        """
        Validate performance against Phase 4 targets.
        
        Returns:
            Dictionary of target validations
        """
        metrics = self.get_performance_metrics()
        
        return {
            "streaming_intervals_in_target": metrics.target_compliance_rate >= 80,
            "average_interval_optimal": 2.0 <= metrics.average_interval <= 3.0,
            "optimization_score_good": metrics.optimization_score >= 70,
            "sufficient_data_points": metrics.total_updates >= 10,
            "network_adaptation_working": metrics.network_adaptations > 0 or metrics.total_updates < 20
        }
    
    def get_optimization_recommendations(self) -> List[str]:
        """Get recommendations for improving streaming performance."""
        metrics = self.get_performance_metrics()
        recommendations = []
        
        if metrics.target_compliance_rate < 80:
            recommendations.append(
                f"Target compliance rate {metrics.target_compliance_rate:.1f}% is below 80%. "
                "Consider adjusting content type configurations."
            )
        
        if metrics.average_interval < 2.0:
            recommendations.append(
                "Average interval is too fast. Increase base intervals for better user experience."
            )
        elif metrics.average_interval > 3.0:
            recommendations.append(
                "Average interval is too slow. Decrease base intervals for more responsive updates."
            )
        
        if metrics.optimization_score < 70:
            recommendations.append(
                f"Optimization score {metrics.optimization_score:.1f} is low. "
                "Review content classification and network adaptation strategies."
            )
        
        if not recommendations:
            recommendations.append("Streaming performance is optimal!")
        
        return recommendations


class TelegramStreamHandler:
    """
    Telegram-specific streaming handler using StreamingOptimizer.
    
    Integrates with the Telegram bot to provide optimized streaming updates
    for different types of content and conversation contexts.
    """
    
    def __init__(self):
        self.optimizer = StreamingOptimizer()
        self.active_streams: Dict[str, Dict] = {}
    
    def start_stream(self, chat_id: str, initial_content: str) -> float:
        """
        Start a new streaming session.
        
        Args:
            chat_id: Telegram chat ID
            initial_content: Initial content to stream
            
        Returns:
            Recommended initial interval
        """
        context = {
            'chat_id': chat_id,
            'is_streaming': True,
            'stream_start': time.time()
        }
        
        interval = self.optimizer.optimize_streaming_rate(initial_content, context)
        
        self.active_streams[chat_id] = {
            'start_time': time.time(),
            'last_update': time.time(),
            'update_count': 1,
            'total_content_size': len(initial_content)
        }
        
        return interval
    
    def update_stream(self, chat_id: str, new_content: str) -> float:
        """
        Update an existing stream with new content.
        
        Args:
            chat_id: Telegram chat ID
            new_content: New content to add to stream
            
        Returns:
            Recommended interval for next update
        """
        if chat_id not in self.active_streams:
            return self.start_stream(chat_id, new_content)
        
        stream_data = self.active_streams[chat_id]
        stream_data['update_count'] += 1
        stream_data['total_content_size'] += len(new_content)
        stream_data['last_update'] = time.time()
        
        context = {
            'chat_id': chat_id,
            'is_streaming': True,
            'stream_duration': time.time() - stream_data['start_time'],
            'update_count': stream_data['update_count']
        }
        
        return self.optimizer.optimize_streaming_rate(new_content, context)
    
    def end_stream(self, chat_id: str):
        """End a streaming session and clean up."""
        if chat_id in self.active_streams:
            del self.active_streams[chat_id]
    
    def get_stream_metrics(self, chat_id: str) -> Optional[Dict]:
        """Get metrics for a specific stream."""
        if chat_id not in self.active_streams:
            return None
        
        stream_data = self.active_streams[chat_id]
        duration = time.time() - stream_data['start_time']
        
        return {
            'duration': duration,
            'update_count': stream_data['update_count'],
            'average_update_rate': stream_data['update_count'] / duration if duration > 0 else 0,
            'total_content_size': stream_data['total_content_size']
        }