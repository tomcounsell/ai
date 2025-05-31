"""Token tracking decorators and integration helpers.

This module provides decorators and helper functions to easily integrate
token usage tracking into existing LLM workflows.
"""

import functools
import uuid
import logging
from datetime import datetime
from typing import Callable, Optional, Any, Dict, Union, Protocol
from dataclasses import dataclass

try:
    from .token_tracker import log_token_usage
except ImportError:
    from token_tracker import log_token_usage

logger = logging.getLogger(__name__)


@dataclass
class TokenUsage:
    """Standard token usage data structure."""
    input_tokens: int
    output_tokens: int
    total_tokens: Optional[int] = None
    
    def __post_init__(self):
        if self.total_tokens is None:
            self.total_tokens = self.input_tokens + self.output_tokens


class LLMResponse(Protocol):
    """Protocol for LLM response objects with token usage."""
    def get_usage(self) -> TokenUsage:
        """Return token usage for this response."""
        ...


def track_tokens(
    project: str,
    host: str,
    model: str,
    user_id: Optional[str] = None,
    auto_request_id: bool = True,
    extract_usage: Optional[Callable[[Any], TokenUsage]] = None
):
    """Decorator to automatically track token usage for LLM calls.
    
    Args:
        project: Project name for tracking
        host: AI provider (e.g., 'Anthropic', 'OpenAI', 'Ollama')
        model: Model name (e.g., 'claude-3-5-sonnet-20241022', 'gpt-4o')
        user_id: Optional user identifier
        auto_request_id: Generate automatic request ID if True
        extract_usage: Function to extract TokenUsage from response
    
    Returns:
        Decorated function that logs token usage
    
    Usage:
        @track_tokens('my_project', 'OpenAI', 'gpt-4o')
        def my_llm_call(prompt: str) -> dict:
            # Your LLM call implementation
            return response_with_usage
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            request_id = str(uuid.uuid4()) if auto_request_id else None
            
            try:
                # Call the original function
                result = func(*args, **kwargs)
                
                # Extract token usage
                if extract_usage:
                    usage = extract_usage(result)
                elif hasattr(result, 'get_usage'):
                    usage = result.get_usage()
                elif isinstance(result, dict) and 'usage' in result:
                    usage_data = result['usage']
                    if isinstance(usage_data, dict):
                        usage = TokenUsage(
                            input_tokens=usage_data.get('input_tokens', 0),
                            output_tokens=usage_data.get('output_tokens', 0)
                        )
                    else:
                        usage = usage_data
                else:
                    logger.warning(f"Could not extract token usage from {type(result)}")
                    return result
                
                # Log the usage
                log_token_usage(
                    project=project,
                    host=host,
                    model=model,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    user_id=user_id,
                    request_id=request_id
                )
                
                logger.debug(f"Tracked {usage.total_tokens} tokens for {project}/{host}/{model}")
                
                return result
                
            except Exception as e:
                logger.error(f"Error in token tracking decorator: {e}")
                # Re-raise the original exception
                raise
        
        return wrapper
    return decorator


class TokenTrackingContext:
    """Context manager for tracking token usage in code blocks."""
    
    def __init__(
        self,
        project: str,
        host: str,
        model: str,
        user_id: Optional[str] = None,
        request_id: Optional[str] = None
    ):
        self.project = project
        self.host = host
        self.model = model
        self.user_id = user_id
        self.request_id = request_id or str(uuid.uuid4())
        self.usage: Optional[TokenUsage] = None
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.usage:
            log_token_usage(
                project=self.project,
                host=self.host,
                model=self.model,
                input_tokens=self.usage.input_tokens,
                output_tokens=self.usage.output_tokens,
                user_id=self.user_id,
                request_id=self.request_id
            )
    
    def set_usage(self, input_tokens: int, output_tokens: int):
        """Set token usage to be logged when context exits."""
        self.usage = TokenUsage(input_tokens, output_tokens)
    
    def add_usage(self, input_tokens: int, output_tokens: int):
        """Add to existing token usage."""
        if self.usage is None:
            self.usage = TokenUsage(input_tokens, output_tokens)
        else:
            self.usage.input_tokens += input_tokens
            self.usage.output_tokens += output_tokens
            self.usage.total_tokens = self.usage.input_tokens + self.usage.output_tokens


# Pre-configured decorators for common providers
def track_anthropic_tokens(
    project: str,
    model: str = "claude-3-5-sonnet-20241022",
    user_id: Optional[str] = None
):
    """Convenience decorator for Anthropic Claude models."""
    return track_tokens(
        project, "Anthropic", model, user_id,
        extract_usage=extract_anthropic_usage
    )


def track_openai_tokens(
    project: str,
    model: str = "gpt-4o",
    user_id: Optional[str] = None
):
    """Convenience decorator for OpenAI models."""
    return track_tokens(
        project, "OpenAI", model, user_id,
        extract_usage=extract_openai_usage
    )


def track_ollama_tokens(
    project: str,
    model: str = "llama3.2",
    user_id: Optional[str] = None
):
    """Convenience decorator for Ollama models."""
    return track_tokens(project, "Ollama", model, user_id)


# Helper functions for common integration patterns
def extract_openai_usage(response: Any) -> TokenUsage:
    """Extract token usage from OpenAI response."""
    if hasattr(response, 'usage'):
        usage = response.usage
        return TokenUsage(
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens
        )
    raise ValueError("No usage information found in OpenAI response")


def extract_anthropic_usage(response: Any) -> TokenUsage:
    """Extract token usage from Anthropic response."""
    if hasattr(response, 'usage'):
        usage = response.usage
        return TokenUsage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens
        )
    raise ValueError("No usage information found in Anthropic response")


def extract_pydantic_ai_usage(result: Any) -> TokenUsage:
    """Extract token usage from PydanticAI result."""
    if hasattr(result, 'usage') and callable(result.usage):
        usage = result.usage()
        return TokenUsage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens
        )
    raise ValueError("No usage information found in PydanticAI result")


# Batch tracking for multiple calls
class BatchTokenTracker:
    """Track token usage across multiple LLM calls in a batch."""
    
    def __init__(
        self,
        project: str,
        user_id: Optional[str] = None,
        batch_id: Optional[str] = None
    ):
        self.project = project
        self.user_id = user_id
        self.batch_id = batch_id or str(uuid.uuid4())
        self.calls: list = []
    
    def track_call(
        self,
        host: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        request_id: Optional[str] = None
    ):
        """Track a single call in the batch."""
        call_data = {
            'host': host,
            'model': model,
            'input_tokens': input_tokens,
            'output_tokens': output_tokens,
            'request_id': request_id or f"{self.batch_id}_{len(self.calls)}"
        }
        self.calls.append(call_data)
    
    def log_batch(self):
        """Log all tracked calls to the database."""
        for call in self.calls:
            log_token_usage(
                project=self.project,
                host=call['host'],
                model=call['model'],
                input_tokens=call['input_tokens'],
                output_tokens=call['output_tokens'],
                user_id=self.user_id,
                request_id=call['request_id']
            )
        
        logger.info(f"Logged batch {self.batch_id} with {len(self.calls)} calls")
        return len(self.calls)
    
    def get_batch_summary(self) -> Dict[str, Union[int, float]]:
        """Get summary statistics for the batch."""
        if not self.calls:
            return {'total_calls': 0, 'total_tokens': 0}
        
        total_input = sum(call['input_tokens'] for call in self.calls)
        total_output = sum(call['output_tokens'] for call in self.calls)
        
        return {
            'total_calls': len(self.calls),
            'total_input_tokens': total_input,
            'total_output_tokens': total_output,
            'total_tokens': total_input + total_output,
            'avg_tokens_per_call': (total_input + total_output) / len(self.calls)
        }


# Integration helpers for specific frameworks
def create_pydantic_ai_tracker(project: str, user_id: Optional[str] = None):
    """Create a decorator specifically for PydanticAI agents."""
    def track_pydantic_ai(host: str, model: str):
        return track_tokens(
            project=project,
            host=host,
            model=model,
            user_id=user_id,
            extract_usage=extract_pydantic_ai_usage
        )
    return track_pydantic_ai


def create_openai_tracker(project: str, user_id: Optional[str] = None):
    """Create a decorator specifically for OpenAI API calls."""
    def track_openai_call(model: str = "gpt-4o"):
        return track_tokens(
            project=project,
            host="OpenAI",
            model=model,
            user_id=user_id,
            extract_usage=extract_openai_usage
        )
    return track_openai_call


def create_anthropic_tracker(project: str, user_id: Optional[str] = None):
    """Create a decorator specifically for Anthropic API calls."""
    def track_anthropic_call(model: str = "claude-3-5-sonnet-20241022"):
        return track_tokens(
            project=project,
            host="Anthropic",
            model=model,
            user_id=user_id,
            extract_usage=extract_anthropic_usage
        )
    return track_anthropic_call


# Simple function wrappers for manual tracking
def track_manual_usage(
    project: str,
    host: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    user_id: Optional[str] = None,
    request_id: Optional[str] = None
) -> int:
    """Manually track token usage without decorators.
    
    Returns the record ID from the database.
    """
    return log_token_usage(
        project=project,
        host=host,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        user_id=user_id,
        request_id=request_id
    )