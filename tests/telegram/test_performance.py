"""Performance Tests for Telegram Communication Layer

Performance validation tests ensuring <2s response time and
system scalability requirements are met.
"""

import asyncio
import pytest
import time
import statistics
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, MagicMock

from integrations.telegram.unified_processor import UnifiedProcessor, ProcessingRequest
from integrations.telegram.components.security_gate import SecurityGate
from integrations.telegram.components.context_builder import ContextBuilder
from integrations.telegram.components.type_router import TypeRouter
from integrations.telegram.components.agent_orchestrator import AgentOrchestrator
from integrations.telegram.components.response_manager import ResponseManager


class MockMessage:
    def __init__(self, message_id=1, text="Test message"):
        self.id = message_id
        self.message = text


class MockUser:
    def __init__(self, user_id=1):
        self.id = user_id


@pytest.mark.performance
class TestUnifiedProcessorPerformance:
    """Performance tests for the unified processor"""
    
    @pytest.fixture
    async def optimized_processor(self):
        """Create an optimized processor for performance testing"""
        
        # Use fast mock components for performance testing
        security_gate = AsyncMock()
        security_gate.validate_request.return_value = MagicMock(
            allowed=True, risk_score=0.1
        )
        
        context_builder = AsyncMock()
        context_builder.build_context.return_value = MagicMock(
            message_id=1, chat_id=1, user_id=1, timestamp=time.time()
        )
        
        type_router = AsyncMock()
        type_router.route_message.return_value = MagicMock(
            message_type="text_casual", confidence=0.8
        )
        
        agent_orchestrator = AsyncMock()
        agent_orchestrator.orchestrate.return_value = MagicMock(
            success=True, primary_response="Fast response"
        )
        
        response_manager = AsyncMock()
        response_manager.format_response.return_value = [
            MagicMock(text="Formatted response", chat_id=1)
        ]
        
        return UnifiedProcessor(
            security_gate=security_gate,
            context_builder=context_builder,
            type_router=type_router,
            agent_orchestrator=agent_orchestrator,
            response_manager=response_manager,
            performance_target_ms=2000
        )
    
    @pytest.mark.asyncio
    async def test_single_message_response_time(self, optimized_processor):
        """Test response time for single message processing"""
        
        request = ProcessingRequest(
            message=MockMessage(),
            user=MockUser(),
            chat_id=1,
            message_id=1,
            raw_text="Hello, how are you?"
        )
        
        # Measure processing time
        start_time = time.perf_counter()
        result = await optimized_processor.process_message(request)
        processing_time_ms = (time.perf_counter() - start_time) * 1000
        
        # Verify success and performance
        assert result.success, f"Processing failed: {result.error}"
        assert processing_time_ms < 2000, f"Response time {processing_time_ms:.1f}ms exceeds 2s target"
        
        print(f"Single message processing time: {processing_time_ms:.1f}ms")
    
    @pytest.mark.asyncio
    async def test_concurrent_message_processing_performance(self, optimized_processor):
        """Test performance under concurrent message load"""
        
        # Create multiple requests
        num_requests = 20
        requests = [
            ProcessingRequest(
                message=MockMessage(i, f"Test message {i}"),
                user=MockUser(i),
                chat_id=i,
                message_id=i,
                raw_text=f"Test message {i}"
            )
            for i in range(num_requests)
        ]
        
        # Process concurrently
        start_time = time.perf_counter()
        tasks = [optimized_processor.process_message(req) for req in requests]
        results = await asyncio.gather(*tasks)
        total_time_ms = (time.perf_counter() - start_time) * 1000
        
        # Analyze performance
        successful_results = [r for r in results if r.success]
        success_rate = len(successful_results) / len(results)
        avg_time_per_message = total_time_ms / num_requests
        
        # Performance assertions
        assert success_rate >= 0.95, f"Success rate {success_rate:.2%} below 95%"
        assert avg_time_per_message < 2000, f"Average response time {avg_time_per_message:.1f}ms exceeds 2s"
        
        print(f"Concurrent processing results:")
        print(f"  Messages: {num_requests}")
        print(f"  Total time: {total_time_ms:.1f}ms")
        print(f"  Success rate: {success_rate:.2%}")
        print(f"  Avg time per message: {avg_time_per_message:.1f}ms")
        print(f"  Throughput: {num_requests / (total_time_ms / 1000):.1f} msg/sec")
    
    @pytest.mark.asyncio
    async def test_burst_load_handling(self, optimized_processor):
        """Test handling burst loads of messages"""
        
        burst_sizes = [5, 10, 25, 50]
        results_summary = []
        
        for burst_size in burst_sizes:
            requests = [
                ProcessingRequest(
                    message=MockMessage(i, f"Burst message {i}"),
                    user=MockUser(i),
                    chat_id=i,
                    message_id=i,
                    raw_text=f"Burst message {i}"
                )
                for i in range(burst_size)
            ]
            
            # Process burst
            start_time = time.perf_counter()
            tasks = [optimized_processor.process_message(req) for req in requests]
            results = await asyncio.gather(*tasks)
            burst_time_ms = (time.perf_counter() - start_time) * 1000
            
            # Analyze burst performance
            successful = sum(1 for r in results if r.success)
            success_rate = successful / burst_size
            avg_time = burst_time_ms / burst_size
            throughput = burst_size / (burst_time_ms / 1000)
            
            results_summary.append({
                'burst_size': burst_size,
                'success_rate': success_rate,
                'avg_time_ms': avg_time,
                'throughput': throughput
            })
            
            # Performance requirements
            assert success_rate >= 0.9, f"Burst {burst_size}: Success rate {success_rate:.2%} below 90%"
            assert avg_time < 2000, f"Burst {burst_size}: Avg time {avg_time:.1f}ms exceeds 2s"
        
        # Print summary
        print("\nBurst load test results:")
        for summary in results_summary:
            print(f"  Burst {summary['burst_size']:2d}: "
                  f"{summary['success_rate']:.1%} success, "
                  f"{summary['avg_time_ms']:6.1f}ms avg, "
                  f"{summary['throughput']:5.1f} msg/sec")
    
    @pytest.mark.asyncio
    async def test_memory_efficiency_under_load(self, optimized_processor):
        """Test memory usage under sustained load"""
        
        import psutil
        import os
        
        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss / 1024 / 1024  # MB
        
        # Process many messages
        num_messages = 100
        batch_size = 10
        
        for batch in range(0, num_messages, batch_size):
            requests = [
                ProcessingRequest(
                    message=MockMessage(i, f"Memory test {i}"),
                    user=MockUser(i),
                    chat_id=i % 5,  # Reuse chat IDs
                    message_id=i,
                    raw_text=f"Memory test message {i}"
                )
                for i in range(batch, min(batch + batch_size, num_messages))
            ]
            
            tasks = [optimized_processor.process_message(req) for req in requests]
            await asyncio.gather(*tasks)
            
            # Force garbage collection
            import gc
            gc.collect()
        
        final_memory = process.memory_info().rss / 1024 / 1024  # MB
        memory_increase = final_memory - initial_memory
        
        print(f"Memory usage:")
        print(f"  Initial: {initial_memory:.1f} MB")
        print(f"  Final: {final_memory:.1f} MB")
        print(f"  Increase: {memory_increase:.1f} MB")
        print(f"  Per message: {memory_increase / num_messages:.3f} MB")
        
        # Memory should not grow excessively
        assert memory_increase < 100, f"Memory increased by {memory_increase:.1f} MB (too much)"
    
    @pytest.mark.asyncio
    async def test_response_time_distribution(self, optimized_processor):
        """Test response time distribution and percentiles"""
        
        num_samples = 50
        response_times = []
        
        for i in range(num_samples):
            request = ProcessingRequest(
                message=MockMessage(i, f"Distribution test {i}"),
                user=MockUser(i),
                chat_id=i % 5,
                message_id=i,
                raw_text=f"Distribution test message {i}"
            )
            
            start_time = time.perf_counter()
            result = await optimized_processor.process_message(request)
            response_time_ms = (time.perf_counter() - start_time) * 1000
            
            if result.success:
                response_times.append(response_time_ms)
        
        # Calculate percentiles
        p50 = statistics.median(response_times)
        p95 = statistics.quantiles(response_times, n=20)[18]  # 95th percentile
        p99 = statistics.quantiles(response_times, n=100)[98]  # 99th percentile
        avg = statistics.mean(response_times)
        std_dev = statistics.stdev(response_times)
        
        print(f"\nResponse time distribution ({len(response_times)} samples):")
        print(f"  Mean: {avg:.1f}ms")
        print(f"  Std Dev: {std_dev:.1f}ms")
        print(f"  P50 (median): {p50:.1f}ms")
        print(f"  P95: {p95:.1f}ms")
        print(f"  P99: {p99:.1f}ms")
        print(f"  Min: {min(response_times):.1f}ms")
        print(f"  Max: {max(response_times):.1f}ms")
        
        # Performance requirements
        assert p95 < 2000, f"P95 response time {p95:.1f}ms exceeds 2s"
        assert p99 < 3000, f"P99 response time {p99:.1f}ms exceeds 3s"
        assert avg < 1000, f"Average response time {avg:.1f}ms exceeds 1s"
    
    @pytest.mark.asyncio
    async def test_sustained_load_performance(self, optimized_processor):
        """Test performance under sustained load over time"""
        
        duration_seconds = 10
        messages_per_second = 5
        total_messages = duration_seconds * messages_per_second
        
        start_time = time.perf_counter()
        successful_messages = 0
        response_times = []
        
        # Generate sustained load
        for i in range(total_messages):
            request = ProcessingRequest(
                message=MockMessage(i, f"Sustained test {i}"),
                user=MockUser(i % 10),  # 10 different users
                chat_id=i % 3,          # 3 different chats
                message_id=i,
                raw_text=f"Sustained load message {i}"
            )
            
            msg_start = time.perf_counter()
            result = await optimized_processor.process_message(request)
            msg_time = (time.perf_counter() - msg_start) * 1000
            
            if result.success:
                successful_messages += 1
                response_times.append(msg_time)
            
            # Maintain target rate
            expected_time = (i + 1) / messages_per_second
            actual_time = time.perf_counter() - start_time
            if actual_time < expected_time:
                await asyncio.sleep(expected_time - actual_time)
        
        total_time = time.perf_counter() - start_time
        success_rate = successful_messages / total_messages
        avg_response_time = statistics.mean(response_times) if response_times else 0
        actual_rate = successful_messages / total_time
        
        print(f"\nSustained load test ({duration_seconds}s at {messages_per_second} msg/s):")
        print(f"  Target messages: {total_messages}")
        print(f"  Successful: {successful_messages}")
        print(f"  Success rate: {success_rate:.2%}")
        print(f"  Avg response time: {avg_response_time:.1f}ms")
        print(f"  Actual rate: {actual_rate:.1f} msg/s")
        print(f"  Total duration: {total_time:.1f}s")
        
        # Performance requirements
        assert success_rate >= 0.95, f"Success rate {success_rate:.2%} below 95%"
        assert avg_response_time < 2000, f"Avg response time {avg_response_time:.1f}ms exceeds 2s"
        assert actual_rate >= messages_per_second * 0.9, f"Rate {actual_rate:.1f} below target"


@pytest.mark.performance
class TestComponentPerformance:
    """Performance tests for individual components"""
    
    @pytest.mark.asyncio
    async def test_security_gate_performance(self):
        """Test security gate performance"""
        
        security_gate = SecurityGate()
        num_requests = 100
        
        start_time = time.perf_counter()
        
        for i in range(num_requests):
            result = await security_gate.validate_request(
                user_id=i % 10,  # 10 different users
                chat_id=i % 5,   # 5 different chats
                message_text=f"Performance test message {i}"
            )
            assert result is not None
        
        total_time_ms = (time.perf_counter() - start_time) * 1000
        avg_time_per_request = total_time_ms / num_requests
        
        print(f"Security gate performance:")
        print(f"  {num_requests} requests in {total_time_ms:.1f}ms")
        print(f"  Average: {avg_time_per_request:.2f}ms per request")
        print(f"  Throughput: {num_requests / (total_time_ms / 1000):.1f} req/sec")
        
        # Security gate should be very fast
        assert avg_time_per_request < 10, f"Security gate too slow: {avg_time_per_request:.2f}ms"
    
    @pytest.mark.asyncio
    async def test_type_router_performance(self):
        """Test type router performance"""
        
        type_router = TypeRouter()
        num_requests = 50
        
        messages = [
            "Hello, how are you?",
            "/help me with commands",
            "What is machine learning?",
            "Write a story about dragons",
            "How to implement binary search?",
            "Show me Python code examples",
            "🎉 Party time! 🎈",
            "Please explain quantum physics",
            "I need help with my project",
            "Can you solve this problem?"
        ]
        
        start_time = time.perf_counter()
        
        for i in range(num_requests):
            message_text = messages[i % len(messages)]
            
            # Create mock message and context
            mock_message = MockMessage(i, message_text)
            mock_context = MagicMock()
            mock_context.text_content = message_text
            mock_context.media_content = None
            
            result = await type_router.route_message(
                mock_message, mock_context, None
            )
            assert result is not None
            assert hasattr(result, 'message_type')
            assert hasattr(result, 'confidence')
        
        total_time_ms = (time.perf_counter() - start_time) * 1000
        avg_time_per_request = total_time_ms / num_requests
        
        print(f"Type router performance:")
        print(f"  {num_requests} requests in {total_time_ms:.1f}ms")
        print(f"  Average: {avg_time_per_request:.2f}ms per request")
        print(f"  Throughput: {num_requests / (total_time_ms / 1000):.1f} req/sec")
        
        # Type router should be reasonably fast
        assert avg_time_per_request < 100, f"Type router too slow: {avg_time_per_request:.2f}ms"
    
    @pytest.mark.asyncio
    async def test_response_manager_performance(self):
        """Test response manager performance"""
        
        response_manager = ResponseManager()
        num_requests = 30
        
        # Create mock agent results
        mock_results = [
            MagicMock(
                success=True,
                agent_name="test_agent",
                primary_response=f"This is test response {i} " * (10 + i % 20),  # Varying lengths
                supplementary_responses={},
                tool_outputs={},
                tools_used=[],
                total_execution_time=0.1
            )
            for i in range(num_requests)
        ]
        
        start_time = time.perf_counter()
        
        for i, agent_result in enumerate(mock_results):
            mock_context = MagicMock()
            mock_context.chat_id = i % 5
            mock_context.message_id = i
            mock_context.processing_hints = {}
            
            responses = await response_manager.format_response(
                agent_result=agent_result,
                context=mock_context,
                target_chat_id=i % 5,
                reply_to_message_id=i
            )
            
            assert len(responses) > 0
            assert responses[0].text is not None
        
        total_time_ms = (time.perf_counter() - start_time) * 1000
        avg_time_per_request = total_time_ms / num_requests
        
        print(f"Response manager performance:")
        print(f"  {num_requests} requests in {total_time_ms:.1f}ms")
        print(f"  Average: {avg_time_per_request:.2f}ms per request")
        print(f"  Throughput: {num_requests / (total_time_ms / 1000):.1f} req/sec")
        
        # Response manager should be reasonably fast
        assert avg_time_per_request < 200, f"Response manager too slow: {avg_time_per_request:.2f}ms"


@pytest.mark.performance
class TestScalabilityRequirements:
    """Tests for scalability requirements and limits"""
    
    @pytest.mark.asyncio
    async def test_concurrent_user_handling(self):
        """Test handling multiple concurrent users"""
        
        processor = UnifiedProcessor(performance_target_ms=2000)
        num_users = 20
        messages_per_user = 3
        
        async def user_session(user_id):
            """Simulate a user session with multiple messages"""
            session_times = []
            
            for msg_idx in range(messages_per_user):
                request = ProcessingRequest(
                    message=MockMessage(
                        user_id * 100 + msg_idx,
                        f"User {user_id} message {msg_idx}"
                    ),
                    user=MockUser(user_id),
                    chat_id=user_id,
                    message_id=user_id * 100 + msg_idx,
                    raw_text=f"User {user_id} says hello {msg_idx}"
                )
                
                start_time = time.perf_counter()
                result = await processor.process_message(request)
                response_time = (time.perf_counter() - start_time) * 1000
                
                if result.success:
                    session_times.append(response_time)
                
                # Small delay between messages from same user
                await asyncio.sleep(0.1)
            
            return session_times
        
        # Run concurrent user sessions
        start_time = time.perf_counter()
        user_tasks = [user_session(user_id) for user_id in range(num_users)]
        user_results = await asyncio.gather(*user_tasks)
        total_time = time.perf_counter() - start_time
        
        # Analyze results
        all_response_times = [time for user_times in user_results for time in user_times]
        successful_messages = len(all_response_times)
        total_messages = num_users * messages_per_user
        
        if successful_messages > 0:
            avg_response_time = statistics.mean(all_response_times)
            p95_response_time = statistics.quantiles(all_response_times, n=20)[18]
        else:
            avg_response_time = float('inf')
            p95_response_time = float('inf')
        
        success_rate = successful_messages / total_messages
        throughput = successful_messages / total_time
        
        print(f"\nConcurrent user test:")
        print(f"  Users: {num_users}")
        print(f"  Messages per user: {messages_per_user}")
        print(f"  Total messages: {total_messages}")
        print(f"  Successful: {successful_messages}")
        print(f"  Success rate: {success_rate:.2%}")
        print(f"  Avg response time: {avg_response_time:.1f}ms")
        print(f"  P95 response time: {p95_response_time:.1f}ms")
        print(f"  Throughput: {throughput:.1f} msg/sec")
        print(f"  Total time: {total_time:.1f}s")
        
        # Scalability requirements
        assert success_rate >= 0.9, f"Success rate {success_rate:.2%} below 90%"
        assert avg_response_time < 2000, f"Avg response time {avg_response_time:.1f}ms exceeds 2s"
        assert throughput >= 5, f"Throughput {throughput:.1f} msg/sec below 5 msg/sec target"
    
    @pytest.mark.asyncio
    async def test_chat_isolation_performance(self):
        """Test performance with multiple isolated chats"""
        
        processor = UnifiedProcessor(performance_target_ms=2000)
        num_chats = 10
        messages_per_chat = 5
        
        async def chat_conversation(chat_id):
            """Simulate conversation in a chat"""
            chat_times = []
            
            for msg_idx in range(messages_per_chat):
                request = ProcessingRequest(
                    message=MockMessage(
                        chat_id * 100 + msg_idx,
                        f"Chat {chat_id} message {msg_idx}: Hello everyone!"
                    ),
                    user=MockUser(chat_id * 10 + (msg_idx % 3)),  # Multiple users per chat
                    chat_id=chat_id,
                    message_id=chat_id * 100 + msg_idx,
                    raw_text=f"Hello from chat {chat_id}, message {msg_idx}"
                )
                
                start_time = time.perf_counter()
                result = await processor.process_message(request)
                response_time = (time.perf_counter() - start_time) * 1000
                
                if result.success:
                    chat_times.append(response_time)
                
                # Simulate natural conversation timing
                await asyncio.sleep(0.05)
            
            return chat_times
        
        # Run concurrent chat conversations
        start_time = time.perf_counter()
        chat_tasks = [chat_conversation(chat_id) for chat_id in range(num_chats)]
        chat_results = await asyncio.gather(*chat_tasks)
        total_time = time.perf_counter() - start_time
        
        # Analyze results
        all_response_times = [time for chat_times in chat_results for time in chat_times]
        successful_messages = len(all_response_times)
        total_messages = num_chats * messages_per_chat
        
        if successful_messages > 0:
            avg_response_time = statistics.mean(all_response_times)
        else:
            avg_response_time = float('inf')
        
        success_rate = successful_messages / total_messages
        
        print(f"\nChat isolation test:")
        print(f"  Chats: {num_chats}")
        print(f"  Messages per chat: {messages_per_chat}")
        print(f"  Total messages: {total_messages}")
        print(f"  Successful: {successful_messages}")
        print(f"  Success rate: {success_rate:.2%}")
        print(f"  Avg response time: {avg_response_time:.1f}ms")
        print(f"  Total time: {total_time:.1f}s")
        
        # Performance should not degrade with multiple chats
        assert success_rate >= 0.95, f"Success rate {success_rate:.2%} below 95%"
        assert avg_response_time < 2000, f"Avg response time {avg_response_time:.1f}ms exceeds 2s"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s", "--tb=short"])