"""Tests for token tracking decorators and integration helpers."""

import os
import tempfile
import unittest
from unittest.mock import Mock, patch, MagicMock
from dataclasses import dataclass
from typing import Optional

from utilities.token_decorators import (
    track_tokens, TokenTrackingContext, BatchTokenTracker,
    track_anthropic_tokens, track_openai_tokens, track_ollama_tokens,
    extract_openai_usage, extract_anthropic_usage, extract_pydantic_ai_usage,
    TokenUsage, track_manual_usage
)
from utilities.token_tracker import TokenTracker


@dataclass
class MockOpenAIUsage:
    """Mock OpenAI usage object."""
    prompt_tokens: int
    completion_tokens: int


@dataclass
class MockOpenAIResponse:
    """Mock OpenAI response object."""
    usage: MockOpenAIUsage
    id: str = "req_123"


@dataclass
class MockAnthropicUsage:
    """Mock Anthropic usage object."""
    input_tokens: int
    output_tokens: int


@dataclass
class MockAnthropicResponse:
    """Mock Anthropic response object."""
    usage: MockAnthropicUsage


class MockPydanticAIResult:
    """Mock PydanticAI result object."""
    def __init__(self, input_tokens: int, output_tokens: int):
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
    
    def usage(self):
        return MockAnthropicUsage(
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens
        )


class TestTokenUsage(unittest.TestCase):
    """Test TokenUsage dataclass."""
    
    def test_token_usage_basic(self):
        """Test basic TokenUsage functionality."""
        usage = TokenUsage(input_tokens=100, output_tokens=50)
        self.assertEqual(usage.input_tokens, 100)
        self.assertEqual(usage.output_tokens, 50)
        self.assertEqual(usage.total_tokens, 150)
    
    def test_token_usage_explicit_total(self):
        """Test TokenUsage with explicit total."""
        usage = TokenUsage(input_tokens=100, output_tokens=50, total_tokens=200)
        self.assertEqual(usage.total_tokens, 200)


class TestTrackTokensDecorator(unittest.TestCase):
    """Test the track_tokens decorator."""
    
    def setUp(self):
        """Set up test database."""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.temp_db.close()
        self.tracker = TokenTracker(self.temp_db.name)
    
    def tearDown(self):
        """Clean up test database."""
        os.unlink(self.temp_db.name)
    
    @patch('utilities.token_decorators.log_token_usage')
    def test_decorator_with_dict_response(self, mock_log):
        """Test decorator with dictionary response containing usage."""
        @track_tokens('test_project', 'OpenAI', 'gpt-4o')
        def mock_llm_call():
            return {
                'content': 'Hello world',
                'usage': {
                    'input_tokens': 100,
                    'output_tokens': 50
                }
            }
        
        result = mock_llm_call()
        
        self.assertEqual(result['content'], 'Hello world')
        mock_log.assert_called_once()
        
        call_args = mock_log.call_args[1]
        self.assertEqual(call_args['project'], 'test_project')
        self.assertEqual(call_args['host'], 'OpenAI')
        self.assertEqual(call_args['model'], 'gpt-4o')
        self.assertEqual(call_args['input_tokens'], 100)
        self.assertEqual(call_args['output_tokens'], 50)
    
    @patch('utilities.token_decorators.log_token_usage')
    def test_decorator_with_custom_extractor(self, mock_log):
        """Test decorator with custom usage extractor."""
        def custom_extract(response):
            return TokenUsage(
                input_tokens=response['prompt_tokens'],
                output_tokens=response['completion_tokens']
            )
        
        @track_tokens(
            'test_project', 'Custom', 'custom-model',
            extract_usage=custom_extract
        )
        def mock_llm_call():
            return {
                'text': 'Response',
                'prompt_tokens': 75,
                'completion_tokens': 125
            }
        
        result = mock_llm_call()
        
        self.assertEqual(result['text'], 'Response')
        mock_log.assert_called_once()
        
        call_args = mock_log.call_args[1]
        self.assertEqual(call_args['input_tokens'], 75)
        self.assertEqual(call_args['output_tokens'], 125)
    
    @patch('utilities.token_decorators.log_token_usage')
    def test_decorator_with_protocol_object(self, mock_log):
        """Test decorator with object implementing get_usage protocol."""
        class MockResponse:
            def get_usage(self):
                return TokenUsage(input_tokens=200, output_tokens=300)
        
        @track_tokens('test_project', 'Test', 'test-model')
        def mock_llm_call():
            return MockResponse()
        
        result = mock_llm_call()
        
        self.assertIsInstance(result, MockResponse)
        mock_log.assert_called_once()
        
        call_args = mock_log.call_args[1]
        self.assertEqual(call_args['input_tokens'], 200)
        self.assertEqual(call_args['output_tokens'], 300)
    
    @patch('utilities.token_decorators.log_token_usage')
    def test_decorator_no_usage_info(self, mock_log):
        """Test decorator when no usage information is available."""
        @track_tokens('test_project', 'Test', 'test-model')
        def mock_llm_call():
            return "Simple string response"
        
        with patch('utilities.token_decorators.logger') as mock_logger:
            result = mock_llm_call()
            
            self.assertEqual(result, "Simple string response")
            mock_log.assert_not_called()
            mock_logger.warning.assert_called_once()
    
    @patch('utilities.token_decorators.log_token_usage')
    def test_decorator_with_user_id(self, mock_log):
        """Test decorator with user ID."""
        @track_tokens('test_project', 'OpenAI', 'gpt-4o', user_id='test_user')
        def mock_llm_call():
            return {'usage': {'input_tokens': 100, 'output_tokens': 50}}
        
        mock_llm_call()
        
        call_args = mock_log.call_args[1]
        self.assertEqual(call_args['user_id'], 'test_user')
    
    @patch('utilities.token_decorators.log_token_usage')
    def test_decorator_exception_handling(self, mock_log):
        """Test that decorator re-raises exceptions from wrapped function."""
        @track_tokens('test_project', 'OpenAI', 'gpt-4o')
        def failing_function():
            raise ValueError("Test error")
        
        with self.assertRaises(ValueError):
            failing_function()
        
        mock_log.assert_not_called()


class TestConvenienceDecorators(unittest.TestCase):
    """Test convenience decorators for different providers."""
    
    @patch('utilities.token_decorators.track_tokens')
    def test_track_anthropic_tokens(self, mock_track_tokens):
        """Test Anthropic convenience decorator."""
        from utilities.token_decorators import extract_anthropic_usage
        track_anthropic_tokens('test_project', 'claude-3-opus', 'user123')
        
        mock_track_tokens.assert_called_once_with(
            'test_project', 'Anthropic', 'claude-3-opus', 'user123',
            extract_usage=extract_anthropic_usage
        )
    
    @patch('utilities.token_decorators.track_tokens')
    def test_track_openai_tokens(self, mock_track_tokens):
        """Test OpenAI convenience decorator."""
        from utilities.token_decorators import extract_openai_usage
        track_openai_tokens('test_project', 'gpt-3.5-turbo', 'user123')
        
        mock_track_tokens.assert_called_once_with(
            'test_project', 'OpenAI', 'gpt-3.5-turbo', 'user123',
            extract_usage=extract_openai_usage
        )
    
    @patch('utilities.token_decorators.track_tokens')
    def test_track_ollama_tokens(self, mock_track_tokens):
        """Test Ollama convenience decorator."""
        track_ollama_tokens('test_project', 'mistral', 'user123')
        
        mock_track_tokens.assert_called_once_with(
            'test_project', 'Ollama', 'mistral', 'user123'
        )


class TestUsageExtractors(unittest.TestCase):
    """Test usage extraction functions."""
    
    def test_extract_openai_usage(self):
        """Test OpenAI usage extraction."""
        response = MockOpenAIResponse(
            usage=MockOpenAIUsage(prompt_tokens=100, completion_tokens=50)
        )
        
        usage = extract_openai_usage(response)
        self.assertEqual(usage.input_tokens, 100)
        self.assertEqual(usage.output_tokens, 50)
        self.assertEqual(usage.total_tokens, 150)
    
    def test_extract_openai_usage_no_usage(self):
        """Test OpenAI usage extraction without usage attribute."""
        response = {"content": "No usage info"}
        
        with self.assertRaises(ValueError):
            extract_openai_usage(response)
    
    def test_extract_anthropic_usage(self):
        """Test Anthropic usage extraction."""
        response = MockAnthropicResponse(
            usage=MockAnthropicUsage(input_tokens=200, output_tokens=100)
        )
        
        usage = extract_anthropic_usage(response)
        self.assertEqual(usage.input_tokens, 200)
        self.assertEqual(usage.output_tokens, 100)
        self.assertEqual(usage.total_tokens, 300)
    
    def test_extract_pydantic_ai_usage(self):
        """Test PydanticAI usage extraction."""
        result = MockPydanticAIResult(input_tokens=150, output_tokens=75)
        
        usage = extract_pydantic_ai_usage(result)
        self.assertEqual(usage.input_tokens, 150)
        self.assertEqual(usage.output_tokens, 75)
        self.assertEqual(usage.total_tokens, 225)


class TestTokenTrackingContext(unittest.TestCase):
    """Test TokenTrackingContext context manager."""
    
    @patch('utilities.token_decorators.log_token_usage')
    def test_context_manager_basic(self, mock_log):
        """Test basic context manager usage."""
        with TokenTrackingContext('test_project', 'OpenAI', 'gpt-4o') as ctx:
            ctx.set_usage(100, 50)
        
        mock_log.assert_called_once()
        call_args = mock_log.call_args[1]
        self.assertEqual(call_args['project'], 'test_project')
        self.assertEqual(call_args['host'], 'OpenAI')
        self.assertEqual(call_args['model'], 'gpt-4o')
        self.assertEqual(call_args['input_tokens'], 100)
        self.assertEqual(call_args['output_tokens'], 50)
    
    @patch('utilities.token_decorators.log_token_usage')
    def test_context_manager_add_usage(self, mock_log):
        """Test adding usage multiple times."""
        with TokenTrackingContext('test_project', 'OpenAI', 'gpt-4o') as ctx:
            ctx.add_usage(50, 25)
            ctx.add_usage(50, 25)
        
        mock_log.assert_called_once()
        call_args = mock_log.call_args[1]
        self.assertEqual(call_args['input_tokens'], 100)
        self.assertEqual(call_args['output_tokens'], 50)
    
    @patch('utilities.token_decorators.log_token_usage')
    def test_context_manager_no_usage(self, mock_log):
        """Test context manager without setting usage."""
        with TokenTrackingContext('test_project', 'OpenAI', 'gpt-4o'):
            pass
        
        mock_log.assert_not_called()
    
    @patch('utilities.token_decorators.log_token_usage')
    def test_context_manager_with_user_id(self, mock_log):
        """Test context manager with user ID."""
        with TokenTrackingContext(
            'test_project', 'OpenAI', 'gpt-4o', user_id='test_user'
        ) as ctx:
            ctx.set_usage(100, 50)
        
        call_args = mock_log.call_args[1]
        self.assertEqual(call_args['user_id'], 'test_user')


class TestBatchTokenTracker(unittest.TestCase):
    """Test BatchTokenTracker for batch operations."""
    
    @patch('utilities.token_decorators.log_token_usage')
    def test_batch_tracker_basic(self, mock_log):
        """Test basic batch tracking."""
        tracker = BatchTokenTracker('test_project', user_id='test_user')
        
        tracker.track_call('OpenAI', 'gpt-4o', 100, 50)
        tracker.track_call('Anthropic', 'claude-3-sonnet', 200, 100)
        
        result = tracker.log_batch()
        
        self.assertEqual(result, 2)
        self.assertEqual(mock_log.call_count, 2)
        
        # Check first call
        first_call = mock_log.call_args_list[0][1]
        self.assertEqual(first_call['project'], 'test_project')
        self.assertEqual(first_call['host'], 'OpenAI')
        self.assertEqual(first_call['model'], 'gpt-4o')
        self.assertEqual(first_call['input_tokens'], 100)
        self.assertEqual(first_call['output_tokens'], 50)
        self.assertEqual(first_call['user_id'], 'test_user')
    
    def test_batch_summary(self):
        """Test batch summary statistics."""
        tracker = BatchTokenTracker('test_project')
        
        tracker.track_call('OpenAI', 'gpt-4o', 100, 50)
        tracker.track_call('Anthropic', 'claude-3-sonnet', 200, 100)
        
        summary = tracker.get_batch_summary()
        
        self.assertEqual(summary['total_calls'], 2)
        self.assertEqual(summary['total_input_tokens'], 300)
        self.assertEqual(summary['total_output_tokens'], 150)
        self.assertEqual(summary['total_tokens'], 450)
        self.assertEqual(summary['avg_tokens_per_call'], 225.0)
    
    def test_empty_batch_summary(self):
        """Test summary for empty batch."""
        tracker = BatchTokenTracker('test_project')
        summary = tracker.get_batch_summary()
        
        self.assertEqual(summary['total_calls'], 0)
        self.assertEqual(summary['total_tokens'], 0)


class TestManualTracking(unittest.TestCase):
    """Test manual tracking functions."""
    
    @patch('utilities.token_decorators.log_token_usage')
    def test_track_manual_usage(self, mock_log):
        """Test manual usage tracking."""
        mock_log.return_value = 123
        
        result = track_manual_usage(
            project='test_project',
            host='OpenAI',
            model='gpt-4o',
            input_tokens=100,
            output_tokens=50,
            user_id='test_user',
            request_id='req_123'
        )
        
        self.assertEqual(result, 123)
        mock_log.assert_called_once_with(
            project='test_project',
            host='OpenAI',
            model='gpt-4o',
            input_tokens=100,
            output_tokens=50,
            user_id='test_user',
            request_id='req_123'
        )


class TestIntegrationScenarios(unittest.TestCase):
    """Test realistic integration scenarios."""
    
    def setUp(self):
        """Set up test database."""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.temp_db.close()
    
    def tearDown(self):
        """Clean up test database."""
        os.unlink(self.temp_db.name)
    
    def test_openai_integration_pattern(self):
        """Test OpenAI integration pattern."""
        @track_openai_tokens('ai_project', model='gpt-4o', user_id='dev_user')
        def call_openai_api(prompt: str):
            # Simulate OpenAI API call
            return MockOpenAIResponse(
                usage=MockOpenAIUsage(prompt_tokens=len(prompt.split()), completion_tokens=20)
            )
        
        with patch('utilities.token_decorators.log_token_usage') as mock_log:
            response = call_openai_api("Hello world test prompt")
            
            self.assertIsInstance(response, MockOpenAIResponse)
            mock_log.assert_called_once()
            
            call_args = mock_log.call_args[1]
            self.assertEqual(call_args['project'], 'ai_project')
            self.assertEqual(call_args['host'], 'OpenAI')
            self.assertEqual(call_args['model'], 'gpt-4o')
            self.assertEqual(call_args['user_id'], 'dev_user')
    
    def test_anthropic_integration_pattern(self):
        """Test Anthropic integration pattern."""
        @track_anthropic_tokens('ai_project', model='claude-3-sonnet')
        def call_anthropic_api(message: str):
            return MockAnthropicResponse(
                usage=MockAnthropicUsage(input_tokens=100, output_tokens=150)
            )
        
        with patch('utilities.token_decorators.log_token_usage') as mock_log:
            response = call_anthropic_api("Test message")
            
            self.assertIsInstance(response, MockAnthropicResponse)
            mock_log.assert_called_once()
            
            call_args = mock_log.call_args[1]
            self.assertEqual(call_args['host'], 'Anthropic')
            self.assertEqual(call_args['model'], 'claude-3-sonnet')
    
    def test_context_manager_pattern(self):
        """Test context manager usage pattern."""
        with patch('utilities.token_decorators.log_token_usage') as mock_log:
            with TokenTrackingContext('batch_project', 'OpenAI', 'gpt-4o') as tracker:
                # Simulate multiple API calls
                tracker.add_usage(50, 25)  # First call
                tracker.add_usage(75, 40)  # Second call
                tracker.add_usage(25, 10)  # Third call
            
            mock_log.assert_called_once()
            call_args = mock_log.call_args[1]
            self.assertEqual(call_args['input_tokens'], 150)
            self.assertEqual(call_args['output_tokens'], 75)
    
    def test_batch_tracking_pattern(self):
        """Test batch tracking usage pattern."""
        with patch('utilities.token_decorators.log_token_usage') as mock_log:
            batch = BatchTokenTracker('bulk_processing', user_id='batch_user')
            
            # Simulate processing multiple items
            for i in range(3):
                batch.track_call('OpenAI', 'gpt-4o-mini', 100 + i*10, 50 + i*5)
            
            batch.log_batch()
            
            self.assertEqual(mock_log.call_count, 3)
            
            # Verify batch summary
            summary = batch.get_batch_summary()
            self.assertEqual(summary['total_calls'], 3)
            self.assertEqual(summary['total_input_tokens'], 330)  # 100+110+120
            self.assertEqual(summary['total_output_tokens'], 165)  # 50+55+60


if __name__ == "__main__":
    unittest.main()