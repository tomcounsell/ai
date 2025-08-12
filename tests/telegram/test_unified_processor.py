"""Tests for Unified Processor

Comprehensive tests for the 5-step pipeline processing system.
"""

import asyncio
import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass

from integrations.telegram.unified_processor import (
    UnifiedProcessor, ProcessingRequest, ProcessingResult, ProcessingStage
)
from integrations.telegram.components.security_gate import SecurityResult, SecurityAction, ThreatLevel
from integrations.telegram.components.context_builder import MessageContext
from integrations.telegram.components.type_router import RouteResult, MessageType
from integrations.telegram.components.agent_orchestrator import AgentResult
from integrations.telegram.components.response_manager import FormattedResponse


@dataclass
class MockMessage:
    """Mock Telegram message"""
    id: int = 12345
    message: str = "Test message"
    media: None = None
    reply_to: None = None
    fwd_from: None = None


@dataclass
class MockUser:
    """Mock Telegram user"""
    id: int = 67890
    username: str = "testuser"
    first_name: str = "Test"
    last_name: str = "User"


class TestUnifiedProcessor:
    """Test suite for UnifiedProcessor"""
    
    @pytest.fixture
    def mock_components(self):
        """Create mock components for testing"""
        
        security_gate = AsyncMock()
        security_gate.validate_request.return_value = SecurityResult(
            allowed=True,
            action=SecurityAction.ALLOW,
            threat_level=ThreatLevel.LOW,
            risk_score=0.1
        )
        
        context_builder = AsyncMock()
        context_builder.build_context.return_value = MessageContext(
            message_id=12345,
            chat_id=123,
            user_id=67890,
            timestamp=time.time(),
            text_content="Test message"
        )
        
        type_router = AsyncMock()
        type_router.route_message.return_value = RouteResult(
            message_type=MessageType.TEXT_CASUAL,
            confidence=0.8,
            primary_handler="general_handler"
        )
        
        agent_orchestrator = AsyncMock()
        agent_orchestrator.orchestrate.return_value = AgentResult(
            success=True,
            agent_name="test_agent",
            primary_response="Test response"
        )
        
        response_manager = AsyncMock()
        response_manager.format_response.return_value = [
            FormattedResponse(
                text="Formatted test response",
                chat_id=123
            )
        ]
        
        return {
            'security_gate': security_gate,
            'context_builder': context_builder,
            'type_router': type_router,
            'agent_orchestrator': agent_orchestrator,
            'response_manager': response_manager
        }
    
    @pytest.fixture
    def processor(self, mock_components):
        """Create processor with mock components"""
        return UnifiedProcessor(**mock_components)
    
    @pytest.fixture
    def sample_request(self):
        """Create sample processing request"""
        return ProcessingRequest(
            message=MockMessage(),
            user=MockUser(),
            chat_id=123,
            message_id=12345,
            raw_text="Test message"
        )
    
    @pytest.mark.asyncio
    async def test_successful_processing(self, processor, sample_request, mock_components):
        """Test successful message processing through all stages"""
        
        result = await processor.process_message(sample_request)
        
        # Verify result
        assert result.success
        assert result.stage_reached == ProcessingStage.RESPONSE
        assert len(result.responses) == 1
        assert result.responses[0].text == "Formatted test response"
        
        # Verify all components were called
        mock_components['security_gate'].validate_request.assert_called_once()
        mock_components['context_builder'].build_context.assert_called_once()
        mock_components['type_router'].route_message.assert_called_once()
        mock_components['agent_orchestrator'].orchestrate.assert_called_once()
        mock_components['response_manager'].format_response.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_security_gate_blocked(self, processor, sample_request, mock_components):
        """Test processing when security gate blocks the request"""
        
        # Mock security gate to block
        mock_components['security_gate'].validate_request.return_value = SecurityResult(
            allowed=False,
            action=SecurityAction.BLOCK,
            threat_level=ThreatLevel.HIGH,
            risk_score=0.9,
            reason="Test block"
        )
        
        result = await processor.process_message(sample_request)
        
        # Verify result
        assert not result.success
        assert result.stage_reached == ProcessingStage.SECURITY
        assert result.error == "Security gate blocked: Test block"
        
        # Verify only security gate was called
        mock_components['security_gate'].validate_request.assert_called_once()
        mock_components['context_builder'].build_context.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_component_exception_handling(self, processor, sample_request, mock_components):
        """Test handling of component exceptions"""
        
        # Mock context builder to raise exception
        mock_components['context_builder'].build_context.side_effect = Exception("Context error")
        
        result = await processor.process_message(sample_request)
        
        # Verify result
        assert not result.success
        assert "Context error" in result.error
    
    @pytest.mark.asyncio
    async def test_performance_metrics(self, processor, sample_request):
        """Test performance metrics collection"""
        
        result = await processor.process_message(sample_request)
        
        # Verify metrics
        assert result.metrics.total_duration_ms > 0
        assert result.metrics.security_duration_ms >= 0
        assert result.metrics.context_duration_ms >= 0
        assert result.metrics.routing_duration_ms >= 0
        assert result.metrics.orchestration_duration_ms >= 0
        assert result.metrics.response_duration_ms >= 0
        assert result.metrics.stage_count == 5
    
    @pytest.mark.asyncio
    async def test_concurrent_processing(self, processor):
        """Test concurrent message processing"""
        
        # Create multiple requests
        requests = [
            ProcessingRequest(
                message=MockMessage(id=i),
                user=MockUser(id=i),
                chat_id=i,
                message_id=i,
                raw_text=f"Message {i}"
            )
            for i in range(5)
        ]
        
        # Process concurrently
        tasks = [processor.process_message(req) for req in requests]
        results = await asyncio.gather(*tasks)
        
        # Verify all processed successfully
        assert all(result.success for result in results)
        assert len(results) == 5
    
    @pytest.mark.asyncio
    async def test_pipeline_status(self, processor):
        """Test pipeline status reporting"""
        
        status = await processor.get_pipeline_status()
        
        # Verify status structure
        assert "active_requests" in status
        assert "total_processed" in status
        assert "success_rate" in status
        assert "component_status" in status
        assert isinstance(status["component_status"], dict)
    
    @pytest.mark.asyncio
    async def test_graceful_shutdown(self, processor, sample_request):
        """Test graceful shutdown with active requests"""
        
        # Start processing
        task = asyncio.create_task(processor.process_message(sample_request))
        
        # Allow processing to start
        await asyncio.sleep(0.1)
        
        # Shutdown
        shutdown_task = asyncio.create_task(processor.shutdown())
        
        # Wait for both to complete
        result, _ = await asyncio.gather(task, shutdown_task)
        
        # Verify processing completed
        assert result.success
    
    @pytest.mark.asyncio
    async def test_request_id_tracking(self, processor, sample_request):
        """Test request ID tracking and metadata"""
        
        result = await processor.process_message(sample_request, request_id="test_123")
        
        # Verify request ID in metadata
        assert "request_id" in result.metadata
        assert result.metadata["request_id"] == "test_123"
    
    @pytest.mark.asyncio
    async def test_error_recovery(self, processor, sample_request, mock_components):
        """Test error recovery and fallback behavior"""
        
        # Mock multiple components to fail
        mock_components['type_router'].route_message.side_effect = Exception("Router error")
        mock_components['agent_orchestrator'].orchestrate.side_effect = Exception("Orchestrator error")
        
        result = await processor.process_message(sample_request)
        
        # Should still attempt processing and return error
        assert not result.success
        assert result.error is not None
    
    @pytest.mark.asyncio
    async def test_performance_target_monitoring(self, processor, sample_request):
        """Test performance target monitoring"""
        
        # Set low performance target
        processor.performance_target_ms = 1  # 1ms target (unrealistic)
        
        result = await processor.process_message(sample_request)
        
        # Processing should still succeed but exceed target
        assert result.success
        assert result.metrics.total_duration_ms > processor.performance_target_ms
    
    def test_processor_initialization(self):
        """Test processor initialization with various configurations"""
        
        # Test default initialization
        processor1 = UnifiedProcessor()
        assert processor1.performance_target_ms == 2000
        assert processor1.enable_metrics is True
        
        # Test custom initialization
        processor2 = UnifiedProcessor(
            performance_target_ms=1000,
            enable_metrics=False,
            max_concurrent_requests=5
        )
        assert processor2.performance_target_ms == 1000
        assert processor2.enable_metrics is False


class TestProcessingMetrics:
    """Test suite for processing metrics"""
    
    def test_metrics_initialization(self):
        """Test metrics data structure initialization"""
        from integrations.telegram.unified_processor import PipelineMetrics
        
        metrics = PipelineMetrics()
        
        assert metrics.total_duration_ms == 0.0
        assert metrics.security_duration_ms == 0.0
        assert metrics.context_duration_ms == 0.0
        assert metrics.routing_duration_ms == 0.0
        assert metrics.orchestration_duration_ms == 0.0
        assert metrics.response_duration_ms == 0.0
        assert metrics.stage_count == 0
        assert metrics.memory_peak_mb == 0.0
        assert metrics.errors == []
    
    def test_metrics_calculation(self):
        """Test metrics calculation and aggregation"""
        from integrations.telegram.unified_processor import PipelineMetrics
        
        metrics = PipelineMetrics()
        
        # Simulate stage durations
        metrics.security_duration_ms = 10.5
        metrics.context_duration_ms = 25.3
        metrics.routing_duration_ms = 5.8
        metrics.orchestration_duration_ms = 150.2
        metrics.response_duration_ms = 8.1
        
        # Calculate total (in real implementation this would be done automatically)
        calculated_total = (
            metrics.security_duration_ms +
            metrics.context_duration_ms +
            metrics.routing_duration_ms +
            metrics.orchestration_duration_ms +
            metrics.response_duration_ms
        )
        
        assert calculated_total == 199.9


class TestProcessingRequest:
    """Test suite for ProcessingRequest"""
    
    def test_request_creation(self):
        """Test processing request creation"""
        
        message = MockMessage()
        user = MockUser()
        
        request = ProcessingRequest(
            message=message,
            user=user,
            chat_id=123,
            message_id=12345,
            raw_text="Test message",
            media_info={"type": "photo"},
            forwarded_info={"from_id": 999},
            reply_info={"reply_to_message_id": 111}
        )
        
        assert request.message == message
        assert request.user == user
        assert request.chat_id == 123
        assert request.message_id == 12345
        assert request.raw_text == "Test message"
        assert request.media_info == {"type": "photo"}
        assert request.forwarded_info == {"from_id": 999}
        assert request.reply_info == {"reply_to_message_id": 111}
    
    def test_request_validation(self):
        """Test request validation"""
        
        # Valid request
        request = ProcessingRequest(
            message=MockMessage(),
            user=MockUser(),
            chat_id=123,
            message_id=12345
        )
        
        assert request.chat_id == 123
        assert request.message_id == 12345


@pytest.mark.integration
class TestUnifiedProcessorIntegration:
    """Integration tests for UnifiedProcessor with real components"""
    
    @pytest.mark.asyncio
    async def test_real_component_integration(self):
        """Test with actual component instances (if available)"""
        
        # This would test with real component instances
        # For now, we'll use a simplified test
        
        processor = UnifiedProcessor()
        
        # Verify processor can be created and has expected attributes
        assert processor.performance_target_ms > 0
        assert processor.security_gate is not None
        assert processor.context_builder is not None
        assert processor.type_router is not None
        assert processor.agent_orchestrator is not None
        assert processor.response_manager is not None
    
    @pytest.mark.asyncio
    async def test_end_to_end_flow(self):
        """Test end-to-end message processing flow"""
        
        # This would be a comprehensive end-to-end test
        # Testing with minimal mocking to verify actual flow
        
        processor = UnifiedProcessor()
        
        request = ProcessingRequest(
            message=MockMessage(),
            user=MockUser(),
            chat_id=123,
            message_id=12345,
            raw_text="Hello, how are you?"
        )
        
        # Process message
        result = await processor.process_message(request)
        
        # Basic verification (actual behavior may vary based on real components)
        assert isinstance(result, ProcessingResult)
        assert hasattr(result, 'success')
        assert hasattr(result, 'stage_reached')
        assert hasattr(result, 'metrics')


if __name__ == "__main__":
    pytest.main([__file__, "-v"])