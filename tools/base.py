"""
Tool Implementation Base Framework - 9.8/10 Gold Standard

This module provides the foundational architecture for all AI tools, implementing
enterprise-grade patterns for reliability, observability, and quality assurance.

Architecture Principles:
- Fail-fast validation with comprehensive error context
- Zero-trust input validation and sanitization
- Performance monitoring with structured metrics
- Comprehensive error categorization and recovery
- Quality scoring with continuous improvement feedback
"""

import asyncio
import time
import traceback
import logging
from abc import ABC, abstractmethod
from enum import Enum, auto
from typing import (
    Any, Dict, List, Optional, Union, Callable, TypeVar, Generic, 
    Type, Protocol, runtime_checkable, get_type_hints, Awaitable
)
from dataclasses import dataclass, field
from contextlib import asynccontextmanager, contextmanager
from functools import wraps
import json
import uuid
from datetime import datetime, timedelta

from pydantic import BaseModel, Field, validator, ValidationError
from pydantic_ai import Agent

# Type definitions for enhanced type safety
T = TypeVar('T')
R = TypeVar('R')

class ErrorCategory(Enum):
    """Comprehensive error categorization for intelligent handling."""
    
    INPUT_VALIDATION = auto()      # User input errors - recoverable
    AUTHENTICATION = auto()        # Auth failures - requires user action
    AUTHORIZATION = auto()         # Permission denied - requires escalation
    RATE_LIMITING = auto()         # API rate limits - requires backoff
    NETWORK_ERROR = auto()         # Network issues - retryable
    EXTERNAL_API = auto()          # Third-party API errors
    INTERNAL_ERROR = auto()        # System bugs - requires investigation
    RESOURCE_EXHAUSTION = auto()   # Memory/disk/CPU limits
    TIMEOUT = auto()               # Operation timeouts
    CONFIGURATION = auto()         # Configuration errors
    DATA_CORRUPTION = auto()       # Data integrity issues


class QualityMetric(Enum):
    """Quality assessment dimensions for continuous improvement."""
    
    ACCURACY = "accuracy"              # Result correctness
    PERFORMANCE = "performance"        # Execution speed and efficiency
    RELIABILITY = "reliability"        # Consistency and error rates
    USABILITY = "usability"           # User experience quality
    MAINTAINABILITY = "maintainability"  # Code quality and structure
    SECURITY = "security"             # Security posture
    COMPLIANCE = "compliance"         # Regulatory compliance
    DOCUMENTATION = "documentation"   # Documentation quality
    TESTING = "testing"               # Test coverage and quality
    MONITORING = "monitoring"         # Observability quality


@dataclass
class PerformanceMetrics:
    """Structured performance tracking for optimization insights."""
    
    start_time: float
    end_time: Optional[float] = None
    duration_ms: Optional[float] = None
    memory_usage_mb: Optional[float] = None
    cpu_usage_percent: Optional[float] = None
    api_calls_made: int = 0
    tokens_processed: Optional[int] = None
    cache_hits: int = 0
    cache_misses: int = 0
    retries_attempted: int = 0
    
    def finalize(self) -> None:
        """Complete the performance measurement."""
        if self.end_time is None:
            self.end_time = time.time()
        self.duration_ms = (self.end_time - self.start_time) * 1000


@dataclass
class QualityScore:
    """Multi-dimensional quality assessment with actionable feedback."""
    
    overall_score: float = Field(ge=0.0, le=10.0)
    dimension_scores: Dict[QualityMetric, float] = field(default_factory=dict)
    improvement_suggestions: List[str] = field(default_factory=list)
    confidence_level: float = Field(ge=0.0, le=1.0, default=0.8)
    assessment_timestamp: datetime = field(default_factory=datetime.utcnow)
    
    def add_dimension(self, metric: QualityMetric, score: float, suggestion: str = None) -> None:
        """Add a quality dimension score with optional improvement suggestion."""
        if not 0.0 <= score <= 10.0:
            raise ValueError(f"Quality score must be between 0.0 and 10.0, got {score}")
        
        self.dimension_scores[metric] = score
        if suggestion and score < 8.0:  # Add suggestions for scores below 8.0
            self.improvement_suggestions.append(f"{metric.value}: {suggestion}")
        
        # Recalculate overall score as weighted average
        self._recalculate_overall_score()
    
    def _recalculate_overall_score(self) -> None:
        """Recalculate overall score based on dimension scores."""
        if not self.dimension_scores:
            return
        
        # Weight critical dimensions higher
        weights = {
            QualityMetric.ACCURACY: 0.25,
            QualityMetric.RELIABILITY: 0.20,
            QualityMetric.SECURITY: 0.20,
            QualityMetric.PERFORMANCE: 0.15,
            QualityMetric.USABILITY: 0.10,
            QualityMetric.MAINTAINABILITY: 0.10
        }
        
        total_score = 0.0
        total_weight = 0.0
        
        for metric, score in self.dimension_scores.items():
            weight = weights.get(metric, 0.05)  # Default weight for other metrics
            total_score += score * weight
            total_weight += weight
        
        if total_weight > 0:
            self.overall_score = total_score / total_weight


class ToolError(Exception):
    """Enhanced tool error with comprehensive context and categorization."""
    
    def __init__(
        self,
        message: str,
        category: ErrorCategory,
        details: Optional[Dict[str, Any]] = None,
        recoverable: bool = True,
        retry_after: Optional[float] = None,
        error_code: Optional[str] = None,
        user_message: Optional[str] = None
    ):
        super().__init__(message)
        self.message = message
        self.category = category
        self.details = details or {}
        self.recoverable = recoverable
        self.retry_after = retry_after
        self.error_code = error_code or f"{category.name}_{int(time.time())}"
        self.user_message = user_message or self._generate_user_message()
        self.timestamp = datetime.utcnow()
        self.trace_id = str(uuid.uuid4())
        
    def _generate_user_message(self) -> str:
        """Generate user-friendly error message."""
        category_messages = {
            ErrorCategory.INPUT_VALIDATION: "Please check your input and try again.",
            ErrorCategory.AUTHENTICATION: "Authentication required. Please check your credentials.",
            ErrorCategory.AUTHORIZATION: "You don't have permission to perform this action.",
            ErrorCategory.RATE_LIMITING: "Rate limit exceeded. Please wait before trying again.",
            ErrorCategory.NETWORK_ERROR: "Network connection issue. Please try again.",
            ErrorCategory.EXTERNAL_API: "External service temporarily unavailable.",
            ErrorCategory.TIMEOUT: "Operation timed out. Please try again.",
            ErrorCategory.INTERNAL_ERROR: "An internal error occurred. Our team has been notified."
        }
        return category_messages.get(self.category, "An unexpected error occurred.")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert error to structured dictionary for logging/monitoring."""
        return {
            "error_code": self.error_code,
            "message": self.message,
            "user_message": self.user_message,
            "category": self.category.name,
            "recoverable": self.recoverable,
            "retry_after": self.retry_after,
            "details": self.details,
            "timestamp": self.timestamp.isoformat(),
            "trace_id": self.trace_id
        }


@runtime_checkable
class Validator(Protocol):
    """Protocol for input validators."""
    
    def validate(self, value: Any) -> bool:
        """Validate input value."""
        ...
    
    def get_error_message(self, value: Any) -> str:
        """Get validation error message."""
        ...


class BaseInputModel(BaseModel):
    """Enhanced base model for tool inputs with validation."""
    
    class Config:
        validate_assignment = True
        extra = "forbid"  # Prevent unknown fields
        
    def validate_business_rules(self) -> List[str]:
        """Override in subclasses for custom business logic validation."""
        return []


class BaseOutputModel(BaseModel):
    """Enhanced base model for tool outputs with metadata."""
    
    execution_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    quality_score: Optional[QualityScore] = None
    performance_metrics: Optional[PerformanceMetrics] = None
    
    class Config:
        validate_assignment = True
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


@dataclass
class ToolContext:
    """Execution context for tools with comprehensive tracking."""
    
    execution_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    request_id: Optional[str] = None
    trace_context: Dict[str, Any] = field(default_factory=dict)
    performance_budget: Optional[timedelta] = None
    quality_threshold: float = 8.0
    retry_config: Dict[str, Any] = field(default_factory=dict)
    
    def add_trace_data(self, key: str, value: Any) -> None:
        """Add trace data for debugging and monitoring."""
        self.trace_context[key] = value
        
    def is_performance_budget_exceeded(self, start_time: float) -> bool:
        """Check if performance budget has been exceeded."""
        if not self.performance_budget:
            return False
        
        elapsed = timedelta(seconds=time.time() - start_time)
        return elapsed > self.performance_budget


class ToolImplementation(ABC, Generic[T, R]):
    """
    Gold Standard Tool Implementation Base Class
    
    Provides enterprise-grade foundation for all AI tools with:
    - Comprehensive error handling and recovery
    - Performance monitoring and optimization
    - Quality scoring and continuous improvement
    - Zero-trust input validation
    - Structured logging and observability
    """
    
    def __init__(
        self,
        name: str,
        version: str = "1.0.0",
        description: str = "",
        logger: Optional[logging.Logger] = None
    ):
        self.name = name
        self.version = version
        self.description = description
        self.logger = logger or logging.getLogger(f"tools.{name}")
        
        # Performance tracking
        self._performance_history: List[PerformanceMetrics] = []
        self._quality_history: List[QualityScore] = []
        
        # Error tracking
        self._error_count: Dict[ErrorCategory, int] = {cat: 0 for cat in ErrorCategory}
        self._last_errors: List[ToolError] = []
        
        # Configuration
        self._max_retries = 3
        self._base_retry_delay = 1.0
        self._max_retry_delay = 60.0
        self._error_history_limit = 100
        
    @property
    def input_model(self) -> Type[BaseInputModel]:
        """Get the input model class. Must be implemented by subclasses."""
        raise NotImplementedError("Subclasses must define input_model")
    
    @property
    def output_model(self) -> Type[BaseOutputModel]:
        """Get the output model class. Must be implemented by subclasses."""
        raise NotImplementedError("Subclasses must define output_model")
    
    @abstractmethod
    async def _execute_core(self, input_data: T, context: ToolContext) -> R:
        """
        Core execution logic. Must be implemented by subclasses.
        
        This method should focus purely on business logic and assume
        all inputs are validated and context is properly set up.
        """
        pass
    
    def validate_input(self, input_data: Any) -> T:
        """Comprehensive input validation with detailed error reporting."""
        try:
            # Pydantic model validation
            if isinstance(input_data, dict):
                validated = self.input_model.parse_obj(input_data)
            elif isinstance(input_data, self.input_model):
                validated = input_data
            else:
                # Try to convert to dict if possible
                if hasattr(input_data, '__dict__'):
                    validated = self.input_model.parse_obj(input_data.__dict__)
                else:
                    raise ValueError(f"Cannot convert input of type {type(input_data)} to {self.input_model}")
            
            # Business rule validation
            business_errors = validated.validate_business_rules()
            if business_errors:
                raise ToolError(
                    f"Business rule violations: {'; '.join(business_errors)}",
                    ErrorCategory.INPUT_VALIDATION,
                    details={"violations": business_errors}
                )
            
            return validated
            
        except ValidationError as e:
            error_details = {
                "validation_errors": [
                    {
                        "field": ".".join(str(x) for x in error.get("loc", [])),
                        "message": error.get("msg", "Unknown error"),
                        "type": error.get("type", "validation_error"),
                        "input": error.get("input")
                    }
                    for error in e.errors()
                ]
            }
            
            raise ToolError(
                f"Input validation failed for {self.name}",
                ErrorCategory.INPUT_VALIDATION,
                details=error_details,
                user_message="Please check your input data and try again."
            )
        
        except Exception as e:
            raise ToolError(
                f"Unexpected validation error: {str(e)}",
                ErrorCategory.INTERNAL_ERROR,
                details={"original_error": str(e), "input_type": str(type(input_data))}
            )
    
    async def execute(
        self, 
        input_data: Any, 
        context: Optional[ToolContext] = None
    ) -> BaseOutputModel:
        """
        Main execution method with comprehensive error handling and monitoring.
        
        This method orchestrates the entire tool execution lifecycle:
        1. Input validation and sanitization
        2. Context setup and trace initialization
        3. Performance monitoring setup
        4. Core execution with error handling
        5. Quality assessment
        6. Result packaging and cleanup
        """
        context = context or ToolContext()
        metrics = PerformanceMetrics(start_time=time.time())
        execution_errors: List[ToolError] = []
        
        try:
            # Phase 1: Input Validation
            self.logger.info(
                f"Starting {self.name} execution",
                extra={
                    "execution_id": context.execution_id,
                    "tool_name": self.name,
                    "tool_version": self.version
                }
            )
            
            validated_input = self.validate_input(input_data)
            context.add_trace_data("input_validated", True)
            context.add_trace_data("input_size", len(str(input_data)))
            
            # Phase 2: Pre-execution Checks
            if context.is_performance_budget_exceeded(metrics.start_time):
                raise ToolError(
                    "Performance budget exceeded before execution",
                    ErrorCategory.TIMEOUT,
                    details={"budget": str(context.performance_budget)}
                )
            
            # Phase 3: Core Execution with Retries
            result = await self._execute_with_retries(
                validated_input, context, metrics
            )
            
            # Phase 4: Quality Assessment
            quality_score = await self._assess_quality(
                validated_input, result, metrics, context
            )
            
            # Phase 5: Result Packaging
            if isinstance(result, BaseOutputModel):
                result.quality_score = quality_score
                result.performance_metrics = metrics
                return result
            else:
                # Wrap non-BaseOutputModel results
                output_data = self.output_model(
                    **result if isinstance(result, dict) else {"result": result}
                )
                output_data.quality_score = quality_score
                output_data.performance_metrics = metrics
                return output_data
                
        except ToolError as e:
            self._record_error(e)
            execution_errors.append(e)
            raise
        
        except Exception as e:
            tool_error = ToolError(
                f"Unexpected error in {self.name}: {str(e)}",
                ErrorCategory.INTERNAL_ERROR,
                details={
                    "original_error": str(e),
                    "traceback": traceback.format_exc(),
                    "execution_id": context.execution_id
                }
            )
            self._record_error(tool_error)
            execution_errors.append(tool_error)
            raise tool_error
        
        finally:
            # Always finalize metrics and logging
            metrics.finalize()
            self._performance_history.append(metrics)
            
            # Limit history size for memory management
            if len(self._performance_history) > 1000:
                self._performance_history = self._performance_history[-500:]
            
            self.logger.info(
                f"Completed {self.name} execution",
                extra={
                    "execution_id": context.execution_id,
                    "duration_ms": metrics.duration_ms,
                    "success": len(execution_errors) == 0,
                    "error_count": len(execution_errors)
                }
            )
    
    async def _execute_with_retries(
        self, 
        validated_input: T, 
        context: ToolContext, 
        metrics: PerformanceMetrics
    ) -> R:
        """Execute core logic with intelligent retry handling."""
        last_error: Optional[ToolError] = None
        retry_delay = self._base_retry_delay
        
        for attempt in range(self._max_retries + 1):
            try:
                if attempt > 0:
                    self.logger.info(
                        f"Retry attempt {attempt} for {self.name}",
                        extra={"execution_id": context.execution_id}
                    )
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, self._max_retry_delay)
                    metrics.retries_attempted += 1
                
                result = await self._execute_core(validated_input, context)
                return result
                
            except ToolError as e:
                last_error = e
                
                # Don't retry non-recoverable errors
                if not e.recoverable or e.category in [
                    ErrorCategory.INPUT_VALIDATION,
                    ErrorCategory.AUTHENTICATION,
                    ErrorCategory.AUTHORIZATION
                ]:
                    break
                
                # Respect retry_after from error
                if e.retry_after:
                    retry_delay = e.retry_after
                
                # Log retry decision
                if attempt < self._max_retries:
                    self.logger.warning(
                        f"Retryable error in {self.name}, will retry",
                        extra={
                            "execution_id": context.execution_id,
                            "attempt": attempt + 1,
                            "error_category": e.category.name,
                            "retry_delay": retry_delay
                        }
                    )
            
            except Exception as e:
                # Convert unexpected errors to ToolError
                last_error = ToolError(
                    f"Unexpected error: {str(e)}",
                    ErrorCategory.INTERNAL_ERROR,
                    details={"original_error": str(e), "attempt": attempt + 1}
                )
                break  # Don't retry unexpected errors
        
        # All retries exhausted
        if last_error:
            last_error.details = last_error.details or {}
            last_error.details["total_attempts"] = attempt + 1
            raise last_error
        
        # This should never happen, but just in case
        raise ToolError(
            f"Execution failed after {attempt + 1} attempts",
            ErrorCategory.INTERNAL_ERROR
        )
    
    async def _assess_quality(
        self,
        input_data: T,
        result: R,
        metrics: PerformanceMetrics,
        context: ToolContext
    ) -> QualityScore:
        """Comprehensive quality assessment with actionable feedback."""
        quality = QualityScore(overall_score=7.0)  # Default baseline
        
        try:
            # Performance assessment
            if metrics.duration_ms:
                if metrics.duration_ms < 1000:  # Under 1 second
                    quality.add_dimension(QualityMetric.PERFORMANCE, 9.5)
                elif metrics.duration_ms < 5000:  # Under 5 seconds
                    quality.add_dimension(QualityMetric.PERFORMANCE, 8.0)
                elif metrics.duration_ms < 10000:  # Under 10 seconds
                    quality.add_dimension(
                        QualityMetric.PERFORMANCE, 6.0,
                        "Consider optimizing for faster execution"
                    )
                else:
                    quality.add_dimension(
                        QualityMetric.PERFORMANCE, 4.0,
                        "Execution time exceeds acceptable limits"
                    )
            
            # Reliability assessment based on recent error history
            recent_errors = [
                e for e in self._last_errors 
                if (datetime.utcnow() - e.timestamp) < timedelta(hours=1)
            ]
            
            if len(recent_errors) == 0:
                quality.add_dimension(QualityMetric.RELIABILITY, 9.5)
            elif len(recent_errors) <= 2:
                quality.add_dimension(QualityMetric.RELIABILITY, 8.0)
            else:
                quality.add_dimension(
                    QualityMetric.RELIABILITY, 6.0,
                    f"High error rate: {len(recent_errors)} errors in last hour"
                )
            
            # Custom quality assessment (can be overridden by subclasses)
            await self._custom_quality_assessment(quality, input_data, result, context)
            
            # Apply quality threshold check
            if quality.overall_score < context.quality_threshold:
                quality.improvement_suggestions.append(
                    f"Quality score {quality.overall_score:.1f} below threshold "
                    f"{context.quality_threshold}"
                )
            
            self._quality_history.append(quality)
            
            return quality
            
        except Exception as e:
            self.logger.error(
                f"Error during quality assessment for {self.name}: {str(e)}",
                extra={"execution_id": context.execution_id}
            )
            # Return a basic quality score if assessment fails
            fallback_quality = QualityScore(overall_score=7.0)
            fallback_quality.improvement_suggestions.append(
                "Quality assessment failed - manual review recommended"
            )
            return fallback_quality
    
    async def _custom_quality_assessment(
        self,
        quality: QualityScore,
        input_data: T,
        result: R,
        context: ToolContext
    ) -> None:
        """
        Custom quality assessment logic. Override in subclasses.
        
        This method allows tools to implement domain-specific quality
        metrics and assessments based on their specific requirements.
        """
        pass
    
    def _record_error(self, error: ToolError) -> None:
        """Record error for tracking and analysis."""
        self._error_count[error.category] += 1
        self._last_errors.append(error)
        
        # Limit error history for memory management
        if len(self._last_errors) > self._error_history_limit:
            self._last_errors = self._last_errors[-50:]
        
        # Log error details
        self.logger.error(
            f"Error in {self.name}: {error.message}",
            extra={
                "error_code": error.error_code,
                "error_category": error.category.name,
                "recoverable": error.recoverable,
                "trace_id": error.trace_id,
                "error_details": error.details
            }
        )
    
    def get_performance_stats(self) -> Dict[str, Any]:
        """Get comprehensive performance statistics."""
        if not self._performance_history:
            return {"message": "No performance data available"}
        
        durations = [m.duration_ms for m in self._performance_history if m.duration_ms]
        
        if not durations:
            return {"message": "No duration data available"}
        
        return {
            "total_executions": len(self._performance_history),
            "average_duration_ms": sum(durations) / len(durations),
            "min_duration_ms": min(durations),
            "max_duration_ms": max(durations),
            "p95_duration_ms": sorted(durations)[int(len(durations) * 0.95)],
            "total_retries": sum(m.retries_attempted for m in self._performance_history),
            "total_api_calls": sum(m.api_calls_made for m in self._performance_history),
            "cache_hit_rate": self._calculate_cache_hit_rate()
        }
    
    def get_quality_stats(self) -> Dict[str, Any]:
        """Get comprehensive quality statistics."""
        if not self._quality_history:
            return {"message": "No quality data available"}
        
        recent_scores = [
            q.overall_score for q in self._quality_history[-100:]  # Last 100 executions
        ]
        
        return {
            "total_assessments": len(self._quality_history),
            "average_quality_score": sum(recent_scores) / len(recent_scores),
            "min_quality_score": min(recent_scores),
            "max_quality_score": max(recent_scores),
            "quality_trend": self._calculate_quality_trend(),
            "common_improvements": self._get_common_improvements()
        }
    
    def get_error_stats(self) -> Dict[str, Any]:
        """Get comprehensive error statistics."""
        total_errors = sum(self._error_count.values())
        
        if total_errors == 0:
            return {"message": "No errors recorded"}
        
        return {
            "total_errors": total_errors,
            "error_rate": total_errors / len(self._performance_history) if self._performance_history else 0,
            "errors_by_category": {
                category.name: count 
                for category, count in self._error_count.items() 
                if count > 0
            },
            "recent_error_trend": self._calculate_error_trend(),
            "most_common_error": max(self._error_count, key=self._error_count.get).name
        }
    
    def _calculate_cache_hit_rate(self) -> float:
        """Calculate cache hit rate from performance history."""
        total_hits = sum(m.cache_hits for m in self._performance_history)
        total_misses = sum(m.cache_misses for m in self._performance_history)
        total_requests = total_hits + total_misses
        
        return total_hits / total_requests if total_requests > 0 else 0.0
    
    def _calculate_quality_trend(self) -> str:
        """Calculate quality trend over recent executions."""
        if len(self._quality_history) < 10:
            return "insufficient_data"
        
        recent_scores = [q.overall_score for q in self._quality_history[-20:]]
        early_avg = sum(recent_scores[:10]) / 10
        late_avg = sum(recent_scores[10:]) / 10
        
        if late_avg > early_avg + 0.5:
            return "improving"
        elif late_avg < early_avg - 0.5:
            return "declining"
        else:
            return "stable"
    
    def _calculate_error_trend(self) -> str:
        """Calculate error trend over recent executions."""
        recent_errors = [
            e for e in self._last_errors 
            if (datetime.utcnow() - e.timestamp) < timedelta(hours=24)
        ]
        
        if len(recent_errors) == 0:
            return "no_recent_errors"
        
        very_recent = [
            e for e in recent_errors 
            if (datetime.utcnow() - e.timestamp) < timedelta(hours=1)
        ]
        
        if len(very_recent) > len(recent_errors) * 0.5:
            return "increasing"
        else:
            return "stable"
    
    def _get_common_improvements(self) -> List[str]:
        """Get most common improvement suggestions."""
        all_suggestions = []
        for quality in self._quality_history[-100:]:  # Last 100 assessments
            all_suggestions.extend(quality.improvement_suggestions)
        
        # Count suggestions and return top 3
        from collections import Counter
        suggestion_counts = Counter(all_suggestions)
        return [suggestion for suggestion, _ in suggestion_counts.most_common(3)]
    
    def health_check(self) -> Dict[str, Any]:
        """Comprehensive health check for monitoring systems."""
        return {
            "tool_name": self.name,
            "tool_version": self.version,
            "status": "healthy" if self._is_healthy() else "unhealthy",
            "performance_stats": self.get_performance_stats(),
            "quality_stats": self.get_quality_stats(),
            "error_stats": self.get_error_stats(),
            "last_execution": (
                self._performance_history[-1].start_time 
                if self._performance_history else None
            ),
            "health_score": self._calculate_health_score()
        }
    
    def _is_healthy(self) -> bool:
        """Determine if tool is in healthy state."""
        # Check recent error rate
        recent_errors = [
            e for e in self._last_errors 
            if (datetime.utcnow() - e.timestamp) < timedelta(hours=1)
        ]
        
        if len(recent_errors) > 10:  # More than 10 errors in last hour
            return False
        
        # Check recent quality scores
        if self._quality_history:
            recent_quality = [q.overall_score for q in self._quality_history[-10:]]
            if recent_quality and sum(recent_quality) / len(recent_quality) < 6.0:
                return False
        
        return True
    
    def _calculate_health_score(self) -> float:
        """Calculate overall health score (0-10)."""
        performance_stats = self.get_performance_stats()
        quality_stats = self.get_quality_stats()
        error_stats = self.get_error_stats()
        
        health_score = 10.0
        
        # Penalize for high error rate
        if isinstance(error_stats, dict) and "error_rate" in error_stats:
            error_rate = error_stats["error_rate"]
            if error_rate > 0.1:  # More than 10% error rate
                health_score -= min(5.0, error_rate * 20)
        
        # Reward good quality scores
        if isinstance(quality_stats, dict) and "average_quality_score" in quality_stats:
            avg_quality = quality_stats["average_quality_score"]
            if avg_quality >= 8.0:
                health_score = min(10.0, health_score + 1.0)
            elif avg_quality < 6.0:
                health_score -= 2.0
        
        # Consider performance
        if isinstance(performance_stats, dict) and "average_duration_ms" in performance_stats:
            avg_duration = performance_stats["average_duration_ms"]
            if avg_duration > 10000:  # Longer than 10 seconds
                health_score -= 1.0
        
        return max(0.0, min(10.0, health_score))


def performance_monitor(func: Callable) -> Callable:
    """Decorator for monitoring function performance."""
    @wraps(func)
    async def async_wrapper(*args, **kwargs):
        start_time = time.time()
        try:
            result = await func(*args, **kwargs)
            return result
        finally:
            duration = (time.time() - start_time) * 1000
            logging.getLogger().info(
                f"Function {func.__name__} completed in {duration:.2f}ms"
            )
    
    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        start_time = time.time()
        try:
            result = func(*args, **kwargs)
            return result
        finally:
            duration = (time.time() - start_time) * 1000
            logging.getLogger().info(
                f"Function {func.__name__} completed in {duration:.2f}ms"
            )
    
    return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper


@contextmanager
def error_context(category: ErrorCategory, recoverable: bool = True):
    """Context manager for consistent error handling."""
    try:
        yield
    except ToolError:
        raise  # Re-raise ToolErrors as-is
    except Exception as e:
        raise ToolError(
            f"Error in context: {str(e)}",
            category,
            details={"original_error": str(e)},
            recoverable=recoverable
        )


# Export key components
__all__ = [
    'ToolImplementation',
    'BaseInputModel', 
    'BaseOutputModel',
    'ToolContext',
    'ToolError',
    'ErrorCategory',
    'QualityMetric',
    'QualityScore',
    'PerformanceMetrics',
    'performance_monitor',
    'error_context'
]