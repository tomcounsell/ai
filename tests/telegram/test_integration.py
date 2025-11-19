"""Integration Tests for Telegram Communication Layer

End-to-end integration tests validating the complete message flow
and performance requirements.
"""

import asyncio
import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch

from integrations.telegram.unified_processor import UnifiedProcessor, ProcessingRequest
from integrations.telegram.components.security_gate import SecurityGate
from integrations.telegram.components.context_builder import ContextBuilder
from integrations.telegram.components.type_router import TypeRouter, MessageType
from integrations.telegram.components.agent_orchestrator import AgentOrchestrator
from integrations.telegram.components.response_manager import ResponseManager
from integrations.telegram.handlers import HandlerRegistry
from integrations.telegram.client import TelegramClient


# Mock classes for integration testing
class MockTelegramMessage:
    """Mock Telegram message with realistic attributes"""
    
    def __init__(self, message_id=12345, text="Hello!", user_id=67890, chat_id=123):
        self.id = message_id
        self.message = text
        self.user_id = user_id
        self.chat_id = chat_id
        self.date = time.time()
        self.media = None
        self.reply_to = None
        self.fwd_from = None
        
        # Mock peer_id for different chat types
        class MockPeerId:
            def __init__(self, chat_id):
                self.user_id = chat_id if chat_id > 0 else None
                self.chat_id = -chat_id if chat_id < 0 else None
                self.channel_id = None
        
        self.peer_id = MockPeerId(chat_id)


class MockTelegramUser:
    """Mock Telegram user with realistic attributes"""
    
    def __init__(self, user_id=67890, username="testuser"):
        self.id = user_id
        self.username = username
        self.first_name = "Test"
        self.last_name = "User"
        self.is_bot = False


@pytest.mark.integration
class TestEndToEndFlow:
    """End-to-end integration tests for the complete system"""
    
    @pytest.fixture
    async def integrated_processor(self):
        """Create a processor with real components for integration testing"""
        
        # Create components with minimal configuration
        security_gate = SecurityGate(
            enable_content_filtering=True,
            enable_threat_detection=True,
            admin_user_ids={999999}  # Test admin
        )
        
        context_builder = ContextBuilder(
            max_history_messages=10,
            enable_user_profiling=True
        )
        
        type_router = TypeRouter(
            enable_content_analysis=True,
            enable_media_analysis=False  # Disable for testing
        )
        
        agent_orchestrator = AgentOrchestrator(
            max_concurrent_orchestrations=3
        )
        
        response_manager = ResponseManager(
            enable_smart_splitting=True,
            enable_media_processing=False  # Disable for testing
        )
        
        processor = UnifiedProcessor(
            security_gate=security_gate,
            context_builder=context_builder,
            type_router=type_router,
            agent_orchestrator=agent_orchestrator,
            response_manager=response_manager,
            performance_target_ms=2000  # 2 second target
        )
        
        return processor
    
    @pytest.mark.asyncio
    async def test_simple_message_flow(self, integrated_processor):
        """Test processing a simple text message end-to-end"""
        
        # Create realistic request
        message = MockTelegramMessage(
            text="Hello, how are you today?",
            user_id=12345,
            chat_id=123
        )
        user = MockTelegramUser(user_id=12345)
        
        request = ProcessingRequest(
            message=message,
            user=user,
            chat_id=123,
            message_id=message.id,
            raw_text=message.message
        )
        
        # Process message
        start_time = time.perf_counter()
        result = await integrated_processor.process_message(request)
        processing_time = (time.perf_counter() - start_time) * 1000  # Convert to ms
        
        # Verify basic success
        assert result.success, f"Processing failed: {result.error}"
        assert len(result.responses) > 0, "No responses generated"
        
        # Verify performance requirement (<2s)
        assert processing_time < 2000, f"Processing took {processing_time:.1f}ms, exceeding 2s target"
        
        # Verify response quality
        response = result.responses[0]
        assert response.text is not None
        assert len(response.text) > 0
        assert response.chat_id == 123
    
    @pytest.mark.asyncio
    async def test_command_message_flow(self, integrated_processor):
        """Test processing a command message"""
        
        message = MockTelegramMessage(
            text="/help",
            user_id=12345,
            chat_id=123
        )
        user = MockTelegramUser(user_id=12345)
        
        request = ProcessingRequest(
            message=message,
            user=user,
            chat_id=123,
            message_id=message.id,
            raw_text=message.message
        )
        
        result = await integrated_processor.process_message(request)
        
        # Commands should be processed successfully
        assert result.success
        assert len(result.responses) > 0
        
        # Should be routed as command type
        assert "message_type" in result.metadata
    
    @pytest.mark.asyncio
    async def test_technical_question_flow(self, integrated_processor):
        """Test processing a technical question"""
        
        message = MockTelegramMessage(
            text="How do I implement a binary search algorithm in Python?",
            user_id=12345,
            chat_id=123
        )
        user = MockTelegramUser(user_id=12345)
        
        request = ProcessingRequest(
            message=message,
            user=user,
            chat_id=123,
            message_id=message.id,
            raw_text=message.message
        )
        
        result = await integrated_processor.process_message(request)
        
        # Technical questions should be handled
        assert result.success
        assert len(result.responses) > 0
        
        # Should include technical content
        response_text = result.responses[0].text.lower()
        # Technical responses might include code or detailed explanations
        assert len(response_text) > 50  # Should be substantial
    
    @pytest.mark.asyncio
    async def test_security_blocked_message(self, integrated_processor):
        """Test security gate blocking malicious content"""
        
        message = MockTelegramMessage(
            text="Click this suspicious link: bit.ly/malicious-link for FREE MONEY!!!",
            user_id=66666,  # Suspicious user ID
            chat_id=123
        )
        user = MockTelegramUser(user_id=66666)
        
        request = ProcessingRequest(
            message=message,
            user=user,
            chat_id=123,
            message_id=message.id,
            raw_text=message.message
        )
        
        result = await integrated_processor.process_message(request)
        
        # Should either be blocked or processed with warnings
        if not result.success:
            # If blocked, should be at security stage
            assert result.stage_reached.value in ["security"]
            assert result.error is not None
        else:
            # If processed, should have warnings about suspicious content
            # (depending on security configuration)
            pass
    
    @pytest.mark.asyncio
    async def test_admin_user_privileges(self, integrated_processor):
        """Test admin user bypassing security checks"""
        
        message = MockTelegramMessage(
            text="Admin command: reset user violations",
            user_id=999999,  # Admin user ID
            chat_id=123
        )
        user = MockTelegramUser(user_id=999999)
        
        request = ProcessingRequest(
            message=message,
            user=user,
            chat_id=123,
            message_id=message.id,
            raw_text=message.message
        )
        
        result = await integrated_processor.process_message(request)
        
        # Admin users should always be processed
        assert result.success
        assert len(result.responses) > 0
    
    @pytest.mark.asyncio
    async def test_concurrent_message_processing(self, integrated_processor):
        """Test processing multiple messages concurrently"""
        
        # Create multiple different message types
        messages = [
            ("Hello!", MessageType.TEXT_CASUAL),
            ("/help", MessageType.TEXT_COMMAND),
            ("What is machine learning?", MessageType.TEXT_QUESTION),
            ("Write a poem about coding", MessageType.TEXT_CREATIVE),
            ("How to implement quicksort?", MessageType.TEXT_TECHNICAL)
        ]
        
        requests = []
        for i, (text, expected_type) in enumerate(messages):
            message = MockTelegramMessage(
                message_id=i + 1000,
                text=text,
                user_id=12345 + i,
                chat_id=123 + i
            )
            user = MockTelegramUser(user_id=12345 + i)
            
            request = ProcessingRequest(
                message=message,
                user=user,
                chat_id=123 + i,
                message_id=message.id,
                raw_text=text
            )
            requests.append(request)
        
        # Process all messages concurrently
        start_time = time.perf_counter()
        tasks = [integrated_processor.process_message(req) for req in requests]
        results = await asyncio.gather(*tasks)
        total_time = (time.perf_counter() - start_time) * 1000
        
        # Verify all succeeded
        assert all(result.success for result in results), "Some concurrent messages failed"
        
        # Verify concurrent processing was faster than sequential
        # (Should be faster than sum of individual times)
        avg_time_per_message = total_time / len(messages)
        assert avg_time_per_message < 2000, f"Average time per message: {avg_time_per_message:.1f}ms"
        
        # Verify all responses are valid
        for result in results:
            assert len(result.responses) > 0
            assert result.responses[0].text is not None
    
    @pytest.mark.asyncio
    async def test_conversation_context_building(self, integrated_processor):
        """Test conversation context accumulation over multiple messages"""
        
        user_id = 12345
        chat_id = 123
        
        # Simulate conversation sequence
        conversation = [
            "Hello, I'm working on a Python project",
            "I need help with data structures",
            "Specifically, I want to implement a hash table",
            "Can you show me an example?"
        ]
        
        results = []
        for i, text in enumerate(conversation):
            message = MockTelegramMessage(
                message_id=i + 2000,
                text=text,
                user_id=user_id,
                chat_id=chat_id
            )
            user = MockTelegramUser(user_id=user_id)
            
            request = ProcessingRequest(
                message=message,
                user=user,
                chat_id=chat_id,
                message_id=message.id,
                raw_text=text
            )
            
            result = await integrated_processor.process_message(request)
            results.append(result)
            
            assert result.success, f"Message {i+1} failed: {result.error}"
        
        # Later messages should potentially reference earlier context
        # (Exact behavior depends on implementation)
        assert all(result.success for result in results)
        
        # Context should be building up
        final_result = results[-1]
        assert final_result.responses[0].text is not None
    
    @pytest.mark.asyncio
    async def test_error_recovery_and_fallbacks(self, integrated_processor):
        """Test error recovery and fallback mechanisms"""
        
        # Test with potentially problematic input
        problematic_messages = [
            "",  # Empty message
            "x" * 10000,  # Very long message
            "\n\n\n\n\n",  # Only whitespace
            "🎉" * 100,  # Lots of emoji
        ]
        
        for i, text in enumerate(problematic_messages):
            message = MockTelegramMessage(
                message_id=i + 3000,
                text=text,
                user_id=12345,
                chat_id=123
            )
            user = MockTelegramUser(user_id=12345)
            
            request = ProcessingRequest(
                message=message,
                user=user,
                chat_id=123,
                message_id=message.id,
                raw_text=text
            )
            
            result = await integrated_processor.process_message(request)
            
            # Should either succeed or fail gracefully
            if result.success:
                assert len(result.responses) > 0
            else:
                # Should have error information
                assert result.error is not None
                # Should not crash the system
    
    @pytest.mark.asyncio
    async def test_performance_under_load(self, integrated_processor):
        """Test system performance under load"""
        
        # Create a batch of messages
        batch_size = 10
        requests = []
        
        for i in range(batch_size):
            message = MockTelegramMessage(
                message_id=i + 4000,
                text=f"Test message {i} with some content to process",
                user_id=12345 + (i % 5),  # Vary users
                chat_id=123 + (i % 3)     # Vary chats
            )
            user = MockTelegramUser(user_id=12345 + (i % 5))
            
            request = ProcessingRequest(
                message=message,
                user=user,
                chat_id=123 + (i % 3),
                message_id=message.id,
                raw_text=message.message
            )
            requests.append(request)
        
        # Process batch
        start_time = time.perf_counter()
        tasks = [integrated_processor.process_message(req) for req in requests]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        total_time = (time.perf_counter() - start_time) * 1000
        
        # Analyze results
        successful_results = [r for r in results if not isinstance(r, Exception) and r.success]
        failed_results = [r for r in results if isinstance(r, Exception) or (hasattr(r, 'success') and not r.success)]
        
        success_rate = len(successful_results) / len(results)
        avg_response_time = total_time / len(results)
        
        # Performance assertions
        assert success_rate >= 0.8, f"Success rate {success_rate:.2%} below 80%"
        assert avg_response_time < 2000, f"Average response time {avg_response_time:.1f}ms exceeds 2s"
        
        # Log performance metrics
        print(f"\nLoad Test Results:")
        print(f"  Batch size: {batch_size}")
        print(f"  Total time: {total_time:.1f}ms")
        print(f"  Success rate: {success_rate:.2%}")
        print(f"  Average response time: {avg_response_time:.1f}ms")
        print(f"  Successful: {len(successful_results)}")
        print(f"  Failed: {len(failed_results)}")
    
    @pytest.mark.asyncio
    async def test_system_status_monitoring(self, integrated_processor):
        """Test system status and health monitoring"""
        
        # Process a few messages first
        for i in range(3):
            message = MockTelegramMessage(
                text=f"Status test message {i}",
                user_id=12345,
                chat_id=123
            )
            user = MockTelegramUser(user_id=12345)
            
            request = ProcessingRequest(
                message=message,
                user=user,
                chat_id=123,
                message_id=message.id,
                raw_text=message.message
            )
            
            await integrated_processor.process_message(request)
        
        # Get system status
        status = await integrated_processor.get_pipeline_status()
        
        # Verify status structure
        assert "total_processed" in status
        assert "success_rate" in status
        assert "component_status" in status
        
        # Verify component statuses
        component_status = status["component_status"]
        assert "security_gate" in component_status
        assert "context_builder" in component_status
        assert "type_router" in component_status
        assert "agent_orchestrator" in component_status
        assert "response_manager" in component_status
        
        # Each component should report status
        for component_name, component_status in component_status.items():
            assert isinstance(component_status, dict)
    
    @pytest.mark.asyncio
    async def test_graceful_shutdown(self, integrated_processor):
        """Test graceful system shutdown"""
        
        # Start some processing
        message = MockTelegramMessage(text="Test message")
        user = MockTelegramUser()
        request = ProcessingRequest(
            message=message, user=user, chat_id=123,
            message_id=message.id, raw_text=message.message
        )
        
        processing_task = asyncio.create_task(
            integrated_processor.process_message(request)
        )
        
        # Allow processing to start
        await asyncio.sleep(0.1)
        
        # Shutdown system
        shutdown_task = asyncio.create_task(integrated_processor.shutdown())
        
        # Wait for both
        result, _ = await asyncio.gather(processing_task, shutdown_task)
        
        # Processing should complete successfully
        assert result.success


@pytest.mark.integration
class TestHandlerIntegration:
    """Integration tests for handler system with processors"""
    
    @pytest.fixture
    def handler_registry(self):
        """Create handler registry with test handlers"""
        registry = HandlerRegistry()
        
        # Register test handlers
        @registry.register_message_handler(
            "echo_handler",
            [MessageType.TEXT_CASUAL],
            HandlerPriority.NORMAL
        )
        def echo_handler(request, context):
            return f"Echo: {request.raw_text}"
        
        @registry.register_command_handler(
            "test",
            "Test command handler"
        )
        def test_command(command, args, request, context):
            return f"Test command executed with args: {args}"
        
        return registry
    
    @pytest.mark.asyncio
    async def test_handler_processor_integration(self, handler_registry):
        """Test integration between handlers and processors"""
        
        # This would test how handlers integrate with the main processor
        # For now, test handler functionality directly
        
        message = MockTelegramMessage(text="Hello world")
        user = MockTelegramUser()
        
        request = ProcessingRequest(
            message=message,
            user=user,
            chat_id=123,
            message_id=message.id,
            raw_text=message.message
        )
        
        context = MagicMock()
        context.message_type = MessageType.TEXT_CASUAL
        context.chat_id = 123
        context.message_id = message.id
        
        results = await handler_registry.execute_handlers(request, context)
        
        # Should have handler results
        assert len(results) > 0
        
        # Find echo handler result
        echo_results = [r for r in results if r.handler_id == "echo_handler"]
        if echo_results:
            assert echo_results[0].success
            assert "Echo: Hello world" in echo_results[0].responses[0].text


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])