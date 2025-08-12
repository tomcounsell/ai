"""
Pipeline-Telegram Integration Tests

Tests the integration between the message processing pipeline and Telegram,
ensuring proper message handling, response formatting, and real-time communication.
"""

import asyncio
import pytest
import json
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

from integrations.telegram.unified_processor import (
    UnifiedProcessor, ProcessingRequest, ProcessingResult, ProcessingStage
)
from integrations.telegram.components.security_gate import SecurityGate, SecurityResult
from integrations.telegram.components.context_builder import ContextBuilder, MessageContext
from integrations.telegram.components.type_router import TypeRouter, MessageType, RouteResult
from integrations.telegram.components.agent_orchestrator import AgentOrchestrator, AgentResult
from integrations.telegram.components.response_manager import ResponseManager, FormattedResponse
from integrations.telegram.client import TelegramClient


class MockTelegramMessage:
    """Mock Telegram message for testing."""
    
    def __init__(self, text: str = "Test message", message_id: int = 1, chat_id: int = 123456789):
        self.text = text
        self.id = message_id
        self.message_id = message_id
        self.chat_id = chat_id
        self.date = datetime.now(timezone.utc)
        self.from_user = MockTelegramUser()
        self.chat = MockTelegramChat(chat_id)
    
    def __str__(self):
        return self.text


class MockTelegramUser:
    """Mock Telegram user for testing."""
    
    def __init__(self, user_id: int = 987654321, username: str = "test_user"):
        self.id = user_id
        self.username = username
        self.first_name = "Test"
        self.last_name = "User"
        self.is_bot = False


class MockTelegramChat:
    """Mock Telegram chat for testing."""
    
    def __init__(self, chat_id: int = 123456789):
        self.id = chat_id
        self.type = "private"
        self.title = "Test Chat"


class MockSecurityGate(SecurityGate):
    """Mock security gate for testing."""
    
    def __init__(self, allow_all: bool = True):
        super().__init__()
        self.allow_all = allow_all
        self.validation_calls = []
    
    async def validate_request(
        self, 
        user_id: int, 
        chat_id: int, 
        message_text: str,
        media_info: Optional[Dict] = None
    ) -> SecurityResult:
        self.validation_calls.append({
            "user_id": user_id,
            "chat_id": chat_id,
            "message_text": message_text,
            "media_info": media_info
        })
        
        if self.allow_all:
            return SecurityResult(
                allowed=True,
                reason="Test mode - all allowed",
                risk_score=0.1,
                metadata={"test_mode": True}
            )
        else:
            return SecurityResult(
                allowed=False,
                reason="Test mode - blocked",
                risk_score=0.9,
                metadata={"test_mode": True}
            )
    
    async def get_status(self):
        return {"status": "mock", "calls": len(self.validation_calls)}
    
    async def shutdown(self):
        pass


class MockContextBuilder(ContextBuilder):
    """Mock context builder for testing."""
    
    def __init__(self):
        super().__init__()
        self.build_calls = []
    
    async def build_context(
        self,
        chat_id: int,
        user_id: int,
        message,
        security_context: SecurityResult
    ) -> MessageContext:
        self.build_calls.append({
            "chat_id": chat_id,
            "user_id": user_id,
            "message": str(message),
            "security_allowed": security_context.allowed
        })
        
        return MessageContext(
            chat_id=chat_id,
            user_id=user_id,
            user_name="test_user",
            message_history=[],
            workspace="test_workspace",
            security_context=security_context.metadata,
            session_metadata={"test": True}
        )
    
    async def get_status(self):
        return {"status": "mock", "contexts_built": len(self.build_calls)}
    
    async def shutdown(self):
        pass


class MockTypeRouter(TypeRouter):
    """Mock type router for testing."""
    
    def __init__(self, default_type: MessageType = MessageType.TEXT):
        super().__init__()
        self.default_type = default_type
        self.routing_calls = []
    
    async def route_message(
        self,
        message,
        context: MessageContext,
        media_info: Optional[Dict] = None
    ) -> RouteResult:
        self.routing_calls.append({
            "message": str(message),
            "chat_id": context.chat_id,
            "media_info": media_info
        })
        
        return RouteResult(
            message_type=self.default_type,
            confidence=0.9,
            metadata={
                "routing_reason": "mock_router",
                "message_length": len(str(message))
            },
            suggested_tools=["mock_tool"]
        )
    
    async def get_status(self):
        return {"status": "mock", "routes_processed": len(self.routing_calls)}
    
    async def shutdown(self):
        pass


class MockAgentOrchestrator(AgentOrchestrator):
    """Mock agent orchestrator for testing."""
    
    def __init__(self, response_content: str = "Mock agent response"):
        super().__init__()
        self.response_content = response_content
        self.orchestration_calls = []
    
    async def orchestrate(
        self,
        message,
        context: MessageContext,
        message_type: MessageType,
        route_metadata: Dict[str, Any]
    ) -> AgentResult:
        self.orchestration_calls.append({
            "message": str(message),
            "message_type": message_type.value,
            "chat_id": context.chat_id,
            "route_metadata": route_metadata
        })
        
        return AgentResult(
            agent_name="mock_agent",
            response_content=self.response_content,
            tools_used=["mock_tool"],
            confidence=0.95,
            metadata={
                "processing_time_ms": 100,
                "tokens_used": 50
            },
            context_updated=True
        )
    
    async def get_status(self):
        return {"status": "mock", "orchestrations": len(self.orchestration_calls)}
    
    async def shutdown(self):
        pass


class MockResponseManager(ResponseManager):
    """Mock response manager for testing."""
    
    def __init__(self):
        super().__init__()
        self.formatting_calls = []
    
    async def format_response(
        self,
        agent_result: AgentResult,
        context: MessageContext,
        target_chat_id: int,
        reply_to_message_id: Optional[int] = None
    ) -> List[FormattedResponse]:
        self.formatting_calls.append({
            "agent_name": agent_result.agent_name,
            "chat_id": target_chat_id,
            "reply_to": reply_to_message_id,
            "content_length": len(agent_result.response_content)
        })
        
        return [
            FormattedResponse(
                message_type="text",
                content=agent_result.response_content,
                chat_id=target_chat_id,
                reply_to_message_id=reply_to_message_id,
                metadata={
                    "formatted_by": "mock_manager",
                    "agent": agent_result.agent_name,
                    "tools_used": agent_result.tools_used
                }
            )
        ]
    
    async def get_status(self):
        return {"status": "mock", "responses_formatted": len(self.formatting_calls)}
    
    async def shutdown(self):
        pass


class TestPipelineTelegramIntegration:
    """Test suite for Pipeline-Telegram integration."""
    
    @pytest.fixture
    def mock_components(self):
        """Create mock components for testing."""
        return {
            "security_gate": MockSecurityGate(),
            "context_builder": MockContextBuilder(),
            "type_router": MockTypeRouter(),
            "agent_orchestrator": MockAgentOrchestrator("Test response from agent"),
            "response_manager": MockResponseManager()
        }
    
    @pytest.fixture
    def unified_processor(self, mock_components):
        """Create unified processor with mock components."""
        processor = UnifiedProcessor(
            security_gate=mock_components["security_gate"],
            context_builder=mock_components["context_builder"],
            type_router=mock_components["type_router"],
            agent_orchestrator=mock_components["agent_orchestrator"],
            response_manager=mock_components["response_manager"],
            performance_target_ms=1000,
            enable_metrics=True
        )
        return processor
    
    @pytest.mark.asyncio
    async def test_basic_message_processing(self, unified_processor: UnifiedProcessor, mock_components):
        """Test basic message processing through the pipeline."""
        # Create test message
        message = MockTelegramMessage("Hello, this is a test message")
        user = MockTelegramUser()
        
        # Create processing request
        request = ProcessingRequest(
            message=message,
            user=user,
            chat_id=message.chat_id,
            message_id=message.id,
            raw_text=message.text
        )
        
        # Process message
        result = await unified_processor.process_message(request)
        
        # Verify successful processing
        assert result.success is True
        assert result.stage_reached == ProcessingStage.RESPONSE
        assert len(result.responses) == 1
        assert result.responses[0].content == "Test response from agent"
        assert result.error is None
        
        # Verify all components were called
        assert len(mock_components["security_gate"].validation_calls) == 1
        assert len(mock_components["context_builder"].build_calls) == 1
        assert len(mock_components["type_router"].routing_calls) == 1
        assert len(mock_components["agent_orchestrator"].orchestration_calls) == 1
        assert len(mock_components["response_manager"].formatting_calls) == 1
    
    @pytest.mark.asyncio
    async def test_security_gate_blocking(self, mock_components):
        """Test message processing when security gate blocks the request."""
        # Configure security gate to block
        mock_components["security_gate"].allow_all = False
        
        processor = UnifiedProcessor(
            security_gate=mock_components["security_gate"],
            context_builder=mock_components["context_builder"],
            type_router=mock_components["type_router"],
            agent_orchestrator=mock_components["agent_orchestrator"],
            response_manager=mock_components["response_manager"]
        )
        
        message = MockTelegramMessage("Blocked message")
        request = ProcessingRequest(
            message=message,
            user=MockTelegramUser(),
            chat_id=message.chat_id,
            message_id=message.id,
            raw_text=message.text
        )
        
        result = await processor.process_message(request)
        
        # Should fail at security stage
        assert result.success is False
        assert result.stage_reached == ProcessingStage.SECURITY
        assert "Security gate blocked" in result.error
        assert len(result.responses) == 0
        
        # Only security gate should be called
        assert len(mock_components["security_gate"].validation_calls) == 1
        assert len(mock_components["context_builder"].build_calls) == 0
    
    @pytest.mark.asyncio
    async def test_concurrent_message_processing(self, unified_processor: UnifiedProcessor):
        """Test concurrent message processing."""
        # Create multiple test messages
        messages = []
        requests = []
        
        for i in range(10):
            message = MockTelegramMessage(f"Concurrent test message {i}", message_id=i+1)
            request = ProcessingRequest(
                message=message,
                user=MockTelegramUser(user_id=1000+i),
                chat_id=message.chat_id + i,
                message_id=message.id,
                raw_text=message.text
            )
            requests.append(request)
        
        # Process all messages concurrently
        results = await asyncio.gather(*[
            unified_processor.process_message(request) for request in requests
        ])
        
        # All should succeed
        assert all(result.success for result in results)
        assert all(result.stage_reached == ProcessingStage.RESPONSE for result in results)
        assert all(len(result.responses) == 1 for result in results)
        
        # Check unique processing
        response_contents = [result.responses[0].content for result in results]
        assert all(content == "Test response from agent" for content in response_contents)
    
    @pytest.mark.asyncio
    async def test_pipeline_performance_metrics(self, unified_processor: UnifiedProcessor):
        """Test performance metrics collection."""
        message = MockTelegramMessage("Performance test message")
        request = ProcessingRequest(
            message=message,
            user=MockTelegramUser(),
            chat_id=message.chat_id,
            message_id=message.id,
            raw_text=message.text
        )
        
        # Process message multiple times
        for i in range(5):
            result = await unified_processor.process_message(request)
            assert result.success
        
        # Check pipeline status and metrics
        status = await unified_processor.get_pipeline_status()
        
        assert status["total_processed"] >= 5
        assert status["success_rate"] == 1.0
        assert status["failure_count"] == 0
        assert status["avg_duration_ms"] > 0
        assert status["active_requests"] == 0
    
    @pytest.mark.asyncio
    async def test_error_handling_in_pipeline(self, mock_components):
        """Test error handling when pipeline components fail."""
        # Create orchestrator that raises an exception
        class FailingOrchestrator(MockAgentOrchestrator):
            async def orchestrate(self, message, context, message_type, route_metadata):
                raise Exception("Simulated orchestrator failure")
        
        mock_components["agent_orchestrator"] = FailingOrchestrator()
        
        processor = UnifiedProcessor(**mock_components)
        
        message = MockTelegramMessage("Error test message")
        request = ProcessingRequest(
            message=message,
            user=MockTelegramUser(),
            chat_id=message.chat_id,
            message_id=message.id,
            raw_text=message.text
        )
        
        result = await processor.process_message(request)
        
        # Should handle error gracefully
        assert result.success is False
        assert "Simulated orchestrator failure" in result.error
        assert len(result.responses) == 0
        
        # Metrics should track the failure
        status = await processor.get_pipeline_status()
        assert status["failure_count"] >= 1
    
    @pytest.mark.asyncio
    async def test_different_message_types(self, mock_components):
        """Test processing different types of messages."""
        test_cases = [
            (MessageType.TEXT, "Simple text message"),
            (MessageType.COMMAND, "/help command"),
            (MessageType.MEDIA, "Message with media"),
            (MessageType.VOICE, "Voice message content"),
            (MessageType.DOCUMENT, "Document message")
        ]
        
        for message_type, content in test_cases:
            # Configure router for specific type
            mock_components["type_router"].default_type = message_type
            
            processor = UnifiedProcessor(**mock_components)
            
            message = MockTelegramMessage(content)
            request = ProcessingRequest(
                message=message,
                user=MockTelegramUser(),
                chat_id=message.chat_id,
                message_id=message.id,
                raw_text=content,
                media_info={"type": message_type.value} if message_type != MessageType.TEXT else None
            )
            
            result = await processor.process_message(request)
            
            assert result.success
            assert result.metadata["message_type"] == message_type.value
    
    @pytest.mark.asyncio
    async def test_context_preservation(self, unified_processor: UnifiedProcessor, mock_components):
        """Test that context is properly preserved through the pipeline."""
        chat_id = 123456789
        user_id = 987654321
        
        # Process first message
        message1 = MockTelegramMessage("First message", chat_id=chat_id)
        request1 = ProcessingRequest(
            message=message1,
            user=MockTelegramUser(user_id=user_id),
            chat_id=chat_id,
            message_id=1,
            raw_text=message1.text
        )
        
        result1 = await unified_processor.process_message(request1)
        assert result1.success
        
        # Process second message from same chat
        message2 = MockTelegramMessage("Second message", chat_id=chat_id)
        request2 = ProcessingRequest(
            message=message2,
            user=MockTelegramUser(user_id=user_id),
            chat_id=chat_id,
            message_id=2,
            raw_text=message2.text
        )
        
        result2 = await unified_processor.process_message(request2)
        assert result2.success
        
        # Context builder should have been called for both messages
        context_calls = mock_components["context_builder"].build_calls
        assert len(context_calls) == 2
        assert all(call["chat_id"] == chat_id for call in context_calls)
        assert all(call["user_id"] == user_id for call in context_calls)
    
    @pytest.mark.asyncio
    async def test_response_formatting(self, unified_processor: UnifiedProcessor, mock_components):
        """Test response formatting and structure."""
        message = MockTelegramMessage("Format test message")
        request = ProcessingRequest(
            message=message,
            user=MockTelegramUser(),
            chat_id=message.chat_id,
            message_id=message.id,
            raw_text=message.text
        )
        
        result = await unified_processor.process_message(request)
        
        assert result.success
        assert len(result.responses) == 1
        
        response = result.responses[0]
        assert response.message_type == "text"
        assert response.content == "Test response from agent"
        assert response.chat_id == message.chat_id
        assert response.reply_to_message_id == message.id
        assert response.metadata is not None
        assert "formatted_by" in response.metadata
    
    @pytest.mark.asyncio
    async def test_pipeline_shutdown(self, unified_processor: UnifiedProcessor):
        """Test graceful pipeline shutdown."""
        # Start some background processing
        message = MockTelegramMessage("Shutdown test")
        request = ProcessingRequest(
            message=message,
            user=MockTelegramUser(),
            chat_id=message.chat_id,
            message_id=message.id,
            raw_text=message.text
        )
        
        # Process a message first
        result = await unified_processor.process_message(request)
        assert result.success
        
        # Shutdown should complete without errors
        await unified_processor.shutdown()
        
        # Check that components were properly shut down
        status = await unified_processor.get_pipeline_status()
        assert status["active_requests"] == 0
    
    @pytest.mark.asyncio
    async def test_media_message_handling(self, mock_components):
        """Test handling of messages with media attachments."""
        # Configure for media message
        mock_components["type_router"].default_type = MessageType.MEDIA
        
        processor = UnifiedProcessor(**mock_components)
        
        message = MockTelegramMessage("Photo message")
        media_info = {
            "type": "photo",
            "file_id": "test_photo_123",
            "file_size": 1024,
            "caption": "Test photo caption"
        }
        
        request = ProcessingRequest(
            message=message,
            user=MockTelegramUser(),
            chat_id=message.chat_id,
            message_id=message.id,
            raw_text=message.text,
            media_info=media_info
        )
        
        result = await unified_processor.process_message(request)
        
        assert result.success
        assert result.metadata["message_type"] == MessageType.MEDIA.value
        
        # Media info should be passed through components
        security_call = mock_components["security_gate"].validation_calls[0]
        assert security_call["media_info"] == media_info
    
    @pytest.mark.asyncio
    async def test_forwarded_message_handling(self, unified_processor: UnifiedProcessor):
        """Test handling of forwarded messages."""
        forwarded_info = {
            "from_chat_id": 111111111,
            "from_message_id": 5,
            "forward_date": datetime.now(timezone.utc).isoformat()
        }
        
        message = MockTelegramMessage("Forwarded message")
        request = ProcessingRequest(
            message=message,
            user=MockTelegramUser(),
            chat_id=message.chat_id,
            message_id=message.id,
            raw_text=message.text,
            forwarded_info=forwarded_info
        )
        
        result = await unified_processor.process_message(request)
        
        assert result.success
        # Forwarded info should be preserved in processing
        assert result.metadata is not None
    
    @pytest.mark.asyncio
    async def test_reply_message_handling(self, unified_processor: UnifiedProcessor):
        """Test handling of reply messages."""
        reply_info = {
            "reply_to_message_id": 10,
            "reply_to_user_id": 555555555,
            "reply_to_text": "Original message being replied to"
        }
        
        message = MockTelegramMessage("Reply message")
        request = ProcessingRequest(
            message=message,
            user=MockTelegramUser(),
            chat_id=message.chat_id,
            message_id=message.id,
            raw_text=message.text,
            reply_info=reply_info
        )
        
        result = await unified_processor.process_message(request)
        
        assert result.success
        # Reply context should be available for processing
        assert result.responses[0].reply_to_message_id == message.id
    
    @pytest.mark.asyncio
    async def test_request_timeout_handling(self, mock_components):
        """Test handling of request timeouts."""
        # Create slow orchestrator
        class SlowOrchestrator(MockAgentOrchestrator):
            async def orchestrate(self, message, context, message_type, route_metadata):
                await asyncio.sleep(2)  # 2 second delay
                return await super().orchestrate(message, context, message_type, route_metadata)
        
        mock_components["agent_orchestrator"] = SlowOrchestrator()
        
        # Create processor with short timeout
        processor = UnifiedProcessor(
            **mock_components,
            performance_target_ms=500  # 500ms target
        )
        
        message = MockTelegramMessage("Slow processing test")
        request = ProcessingRequest(
            message=message,
            user=MockTelegramUser(),
            chat_id=message.chat_id,
            message_id=message.id,
            raw_text=message.text
        )
        
        # Should still complete but be marked as slow
        result = await unified_processor.process_message(request)
        
        # Note: We don't enforce hard timeouts in the current implementation
        # but metrics should reflect slow performance
        assert result.metrics.total_duration_ms > 500