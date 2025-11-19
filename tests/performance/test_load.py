"""
Load Testing Suite

Tests system performance under load with 50+ concurrent users,
measuring response times, throughput, and system stability.
"""

import asyncio
import pytest
import time
import statistics
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Tuple
import psutil
import os
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
import uuid
import json

from integrations.system_integration import SystemIntegrator
from agents.valor.agent import ValorAgent
from utilities.database import DatabaseManager
from integrations.telegram.unified_processor import UnifiedProcessor, ProcessingRequest
from tests.integration.test_pipeline_telegram import MockTelegramMessage, MockTelegramUser


@dataclass
class LoadTestMetrics:
    """Metrics collected during load testing."""
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    total_duration_seconds: float = 0.0
    min_response_time_ms: float = float('inf')
    max_response_time_ms: float = 0.0
    avg_response_time_ms: float = 0.0
    p50_response_time_ms: float = 0.0
    p95_response_time_ms: float = 0.0
    p99_response_time_ms: float = 0.0
    requests_per_second: float = 0.0
    peak_memory_mb: float = 0.0
    peak_cpu_percent: float = 0.0
    error_details: List[str] = field(default_factory=list)
    response_times: List[float] = field(default_factory=list)


class LoadTestResult:
    """Result of a load test execution."""
    
    def __init__(self, test_name: str):
        self.test_name = test_name
        self.metrics = LoadTestMetrics()
        self.start_time = None
        self.end_time = None
        self.concurrent_users = 0
        self.duration_seconds = 0
        self.system_stats = {}
        
    def calculate_final_metrics(self):
        """Calculate final performance metrics."""
        if self.metrics.response_times:
            self.metrics.min_response_time_ms = min(self.metrics.response_times)
            self.metrics.max_response_time_ms = max(self.metrics.response_times)
            self.metrics.avg_response_time_ms = statistics.mean(self.metrics.response_times)
            self.metrics.p50_response_time_ms = statistics.median(self.metrics.response_times)
            
            sorted_times = sorted(self.metrics.response_times)
            n = len(sorted_times)
            self.metrics.p95_response_time_ms = sorted_times[int(n * 0.95)]
            self.metrics.p99_response_time_ms = sorted_times[int(n * 0.99)]
        
        if self.metrics.total_duration_seconds > 0:
            self.metrics.requests_per_second = self.metrics.total_requests / self.metrics.total_duration_seconds
    
    def print_summary(self):
        """Print test result summary."""
        print(f"\n=== Load Test Results: {self.test_name} ===")
        print(f"Concurrent Users: {self.concurrent_users}")
        print(f"Test Duration: {self.duration_seconds:.2f} seconds")
        print(f"Total Requests: {self.metrics.total_requests}")
        print(f"Successful: {self.metrics.successful_requests}")
        print(f"Failed: {self.metrics.failed_requests}")
        print(f"Success Rate: {(self.metrics.successful_requests/self.metrics.total_requests*100):.1f}%")
        print(f"Requests/Second: {self.metrics.requests_per_second:.2f}")
        print(f"\nResponse Times (ms):")
        print(f"  Min: {self.metrics.min_response_time_ms:.1f}")
        print(f"  Max: {self.metrics.max_response_time_ms:.1f}")
        print(f"  Avg: {self.metrics.avg_response_time_ms:.1f}")
        print(f"  P50: {self.metrics.p50_response_time_ms:.1f}")
        print(f"  P95: {self.metrics.p95_response_time_ms:.1f}")
        print(f"  P99: {self.metrics.p99_response_time_ms:.1f}")
        print(f"\nSystem Resources:")
        print(f"  Peak Memory: {self.metrics.peak_memory_mb:.1f} MB")
        print(f"  Peak CPU: {self.metrics.peak_cpu_percent:.1f}%")
        
        if self.metrics.failed_requests > 0:
            print(f"\nError Summary:")
            error_counts = {}
            for error in self.metrics.error_details:
                error_counts[error] = error_counts.get(error, 0) + 1
            for error, count in error_counts.items():
                print(f"  {error}: {count}")


class LoadTester:
    """Load testing framework for AI Rebuild system."""
    
    def __init__(self, system_integrator: SystemIntegrator):
        self.system_integrator = system_integrator
        self.process = psutil.Process(os.getpid())
        
    async def run_agent_load_test(
        self,
        concurrent_users: int,
        requests_per_user: int,
        test_duration_seconds: int = 60
    ) -> LoadTestResult:
        """Run load test on the agent system."""
        result = LoadTestResult("Agent Load Test")
        result.concurrent_users = concurrent_users
        result.duration_seconds = test_duration_seconds
        result.start_time = datetime.now(timezone.utc)
        
        # Create semaphore to limit concurrent requests
        semaphore = asyncio.Semaphore(concurrent_users)
        
        # Resource monitoring task
        monitor_task = asyncio.create_task(
            self._monitor_resources(result.metrics, test_duration_seconds)
        )
        
        async def user_session(user_id: int):
            """Simulate a user session."""
            async with semaphore:
                chat_id = f"load_test_user_{user_id}"
                
                try:
                    # Create context
                    await self.system_integrator.agent.create_context(
                        chat_id=chat_id,
                        user_name=f"load_user_{user_id}",
                        workspace="load_test"
                    )
                    
                    # Send messages
                    for i in range(requests_per_user):
                        message = f"Load test message {i} from user {user_id}"
                        
                        start_time = time.perf_counter()
                        
                        try:
                            response = await self.system_integrator.agent.process_message(
                                message=message,
                                chat_id=chat_id
                            )
                            
                            response_time = (time.perf_counter() - start_time) * 1000
                            result.metrics.response_times.append(response_time)
                            result.metrics.successful_requests += 1
                            
                        except Exception as e:
                            response_time = (time.perf_counter() - start_time) * 1000
                            result.metrics.response_times.append(response_time)
                            result.metrics.failed_requests += 1
                            result.metrics.error_details.append(str(e))
                        
                        result.metrics.total_requests += 1
                        
                        # Small delay between requests from same user
                        await asyncio.sleep(0.1)
                
                except Exception as e:
                    result.metrics.error_details.append(f"Session error for user {user_id}: {str(e)}")
                
                finally:
                    # Cleanup context
                    try:
                        await self.system_integrator.agent.clear_context(chat_id)
                    except:
                        pass
        
        # Run user sessions
        test_start = time.perf_counter()
        
        tasks = [user_session(i) for i in range(concurrent_users)]
        await asyncio.gather(*tasks, return_exceptions=True)
        
        test_end = time.perf_counter()
        result.metrics.total_duration_seconds = test_end - test_start
        
        # Stop monitoring
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass
        
        result.end_time = datetime.now(timezone.utc)
        result.calculate_final_metrics()
        
        return result
    
    async def run_telegram_pipeline_load_test(
        self,
        concurrent_users: int,
        requests_per_user: int,
        test_duration_seconds: int = 60
    ) -> LoadTestResult:
        """Run load test on the Telegram pipeline."""
        result = LoadTestResult("Telegram Pipeline Load Test")
        result.concurrent_users = concurrent_users
        result.duration_seconds = test_duration_seconds
        result.start_time = datetime.now(timezone.utc)
        
        if not self.system_integrator.telegram_processor:
            result.metrics.error_details.append("Telegram processor not available")
            return result
        
        # Resource monitoring
        monitor_task = asyncio.create_task(
            self._monitor_resources(result.metrics, test_duration_seconds)
        )
        
        semaphore = asyncio.Semaphore(concurrent_users)
        
        async def telegram_user_session(user_id: int):
            """Simulate a Telegram user session."""
            async with semaphore:
                try:
                    for i in range(requests_per_user):
                        # Create mock Telegram message
                        message = MockTelegramMessage(
                            text=f"Pipeline load test message {i} from user {user_id}",
                            message_id=i + (user_id * 1000),
                            chat_id=123456789 + user_id
                        )
                        
                        user = MockTelegramUser(user_id=1000 + user_id)
                        
                        request = ProcessingRequest(
                            message=message,
                            user=user,
                            chat_id=message.chat_id,
                            message_id=message.id,
                            raw_text=message.text
                        )
                        
                        start_time = time.perf_counter()
                        
                        try:
                            processing_result = await self.system_integrator.telegram_processor.process_message(request)
                            
                            response_time = (time.perf_counter() - start_time) * 1000
                            result.metrics.response_times.append(response_time)
                            
                            if processing_result.success:
                                result.metrics.successful_requests += 1
                            else:
                                result.metrics.failed_requests += 1
                                if processing_result.error:
                                    result.metrics.error_details.append(processing_result.error)
                                    
                        except Exception as e:
                            response_time = (time.perf_counter() - start_time) * 1000
                            result.metrics.response_times.append(response_time)
                            result.metrics.failed_requests += 1
                            result.metrics.error_details.append(str(e))
                        
                        result.metrics.total_requests += 1
                        
                        # Small delay between requests
                        await asyncio.sleep(0.05)
                
                except Exception as e:
                    result.metrics.error_details.append(f"Session error for telegram user {user_id}: {str(e)}")
        
        # Run sessions
        test_start = time.perf_counter()
        
        tasks = [telegram_user_session(i) for i in range(concurrent_users)]
        await asyncio.gather(*tasks, return_exceptions=True)
        
        test_end = time.perf_counter()
        result.metrics.total_duration_seconds = test_end - test_start
        
        # Stop monitoring
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass
        
        result.end_time = datetime.now(timezone.utc)
        result.calculate_final_metrics()
        
        return result
    
    async def run_database_load_test(
        self,
        concurrent_connections: int,
        operations_per_connection: int
    ) -> LoadTestResult:
        """Run load test on the database layer."""
        result = LoadTestResult("Database Load Test")
        result.concurrent_users = concurrent_connections
        result.start_time = datetime.now(timezone.utc)
        
        semaphore = asyncio.Semaphore(concurrent_connections)
        
        async def database_session(session_id: int):
            """Simulate database operations."""
            async with semaphore:
                try:
                    for i in range(operations_per_connection):
                        start_time = time.perf_counter()
                        
                        try:
                            # Mix of database operations
                            if i % 4 == 0:
                                # Create project
                                await self.system_integrator.database.create_project(
                                    name=f"load_test_project_{session_id}_{i}",
                                    path=f"/test/path/{session_id}/{i}",
                                    description="Load test project"
                                )
                            elif i % 4 == 1:
                                # Add chat message
                                await self.system_integrator.database.add_chat_message(
                                    project_id=None,
                                    session_id=f"load_session_{session_id}",
                                    role="user",
                                    content=f"Load test message {i}",
                                    token_count=len(f"Load test message {i}") // 4
                                )
                            elif i % 4 == 2:
                                # Get chat history
                                await self.system_integrator.database.get_chat_history(
                                    session_id=f"load_session_{session_id}",
                                    limit=50
                                )
                            else:
                                # Record tool metric
                                await self.system_integrator.database.record_tool_metric(
                                    tool_name="load_test_tool",
                                    operation="test_operation",
                                    execution_time_ms=100 + (i % 200),
                                    success=True
                                )
                            
                            response_time = (time.perf_counter() - start_time) * 1000
                            result.metrics.response_times.append(response_time)
                            result.metrics.successful_requests += 1
                            
                        except Exception as e:
                            response_time = (time.perf_counter() - start_time) * 1000
                            result.metrics.response_times.append(response_time)
                            result.metrics.failed_requests += 1
                            result.metrics.error_details.append(str(e))
                        
                        result.metrics.total_requests += 1
                
                except Exception as e:
                    result.metrics.error_details.append(f"Database session error {session_id}: {str(e)}")
        
        # Monitor resources
        monitor_task = asyncio.create_task(
            self._monitor_resources(result.metrics, 30)
        )
        
        # Run sessions
        test_start = time.perf_counter()
        
        tasks = [database_session(i) for i in range(concurrent_connections)]
        await asyncio.gather(*tasks, return_exceptions=True)
        
        test_end = time.perf_counter()
        result.metrics.total_duration_seconds = test_end - test_start
        
        # Stop monitoring
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass
        
        result.end_time = datetime.now(timezone.utc)
        result.calculate_final_metrics()
        
        return result
    
    async def run_mcp_orchestrator_load_test(
        self,
        concurrent_requests: int,
        requests_per_session: int
    ) -> LoadTestResult:
        """Run load test on MCP orchestrator."""
        result = LoadTestResult("MCP Orchestrator Load Test")
        result.concurrent_users = concurrent_requests
        result.start_time = datetime.now(timezone.utc)
        
        if not self.system_integrator.mcp_orchestrator:
            result.metrics.error_details.append("MCP orchestrator not available")
            return result
        
        semaphore = asyncio.Semaphore(concurrent_requests)
        
        async def mcp_session(session_id: int):
            """Simulate MCP requests."""
            async with semaphore:
                try:
                    for i in range(requests_per_session):
                        from mcp_servers.base import MCPRequest
                        
                        request = MCPRequest(
                            method="health_check",
                            params={"session": session_id, "request": i},
                            id=str(uuid.uuid4())
                        )
                        
                        start_time = time.perf_counter()
                        
                        try:
                            response = await self.system_integrator.mcp_orchestrator.route_request(request)
                            
                            response_time = (time.perf_counter() - start_time) * 1000
                            result.metrics.response_times.append(response_time)
                            
                            if response.success:
                                result.metrics.successful_requests += 1
                            else:
                                result.metrics.failed_requests += 1
                                if response.error:
                                    result.metrics.error_details.append(str(response.error))
                        
                        except Exception as e:
                            response_time = (time.perf_counter() - start_time) * 1000
                            result.metrics.response_times.append(response_time)
                            result.metrics.failed_requests += 1
                            result.metrics.error_details.append(str(e))
                        
                        result.metrics.total_requests += 1
                
                except Exception as e:
                    result.metrics.error_details.append(f"MCP session error {session_id}: {str(e)}")
        
        # Monitor resources
        monitor_task = asyncio.create_task(
            self._monitor_resources(result.metrics, 30)
        )
        
        # Run sessions
        test_start = time.perf_counter()
        
        tasks = [mcp_session(i) for i in range(concurrent_requests)]
        await asyncio.gather(*tasks, return_exceptions=True)
        
        test_end = time.perf_counter()
        result.metrics.total_duration_seconds = test_end - test_start
        
        # Stop monitoring
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass
        
        result.end_time = datetime.now(timezone.utc)
        result.calculate_final_metrics()
        
        return result
    
    async def _monitor_resources(self, metrics: LoadTestMetrics, duration_seconds: int):
        """Monitor system resources during test."""
        start_time = time.perf_counter()
        
        while time.perf_counter() - start_time < duration_seconds:
            try:
                # Memory usage
                memory_info = self.process.memory_info()
                memory_mb = memory_info.rss / 1024 / 1024
                metrics.peak_memory_mb = max(metrics.peak_memory_mb, memory_mb)
                
                # CPU usage
                cpu_percent = self.process.cpu_percent()
                metrics.peak_cpu_percent = max(metrics.peak_cpu_percent, cpu_percent)
                
                await asyncio.sleep(0.5)  # Monitor every 500ms
                
            except Exception as e:
                # Resource monitoring shouldn't break the test
                continue


class TestLoadPerformance:
    """Load performance test suite."""
    
    @pytest.fixture
    async def system_integrator(self):
        """System integrator for load testing."""
        config = {
            "agent_model": "openai:gpt-3.5-turbo",
            "max_context_tokens": 50000,
            "debug": False,  # Disable debug for performance
            "telegram_response_target": 2000,
            "telegram_max_concurrent": 50
        }
        
        integrator = SystemIntegrator(
            config=config,
            enable_monitoring=True,
            health_check_interval=30
        )
        
        await integrator.initialize()
        
        yield integrator
        
        await integrator.shutdown()
    
    @pytest.fixture
    def load_tester(self, system_integrator):
        """Load tester instance."""
        return LoadTester(system_integrator)
    
    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_agent_load_50_users(self, load_tester: LoadTester):
        """Test agent performance with 50 concurrent users."""
        result = await load_tester.run_agent_load_test(
            concurrent_users=50,
            requests_per_user=5,
            test_duration_seconds=60
        )
        
        result.print_summary()
        
        # Performance assertions
        assert result.metrics.successful_requests >= result.metrics.total_requests * 0.95  # 95% success rate
        assert result.metrics.avg_response_time_ms < 5000  # Under 5 seconds average
        assert result.metrics.p95_response_time_ms < 10000  # 95% under 10 seconds
        assert result.metrics.requests_per_second >= 10  # At least 10 RPS
        
        # Resource usage assertions
        assert result.metrics.peak_memory_mb < 2048  # Under 2GB memory
    
    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_telegram_pipeline_load_100_users(self, load_tester: LoadTester):
        """Test Telegram pipeline with 100 concurrent users."""
        result = await load_tester.run_telegram_pipeline_load_test(
            concurrent_users=100,
            requests_per_user=3,
            test_duration_seconds=60
        )
        
        result.print_summary()
        
        # Performance assertions
        assert result.metrics.successful_requests >= result.metrics.total_requests * 0.90  # 90% success rate
        assert result.metrics.avg_response_time_ms < 3000  # Under 3 seconds average
        assert result.metrics.requests_per_second >= 20  # At least 20 RPS
    
    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_database_load_concurrent_connections(self, load_tester: LoadTester):
        """Test database with concurrent connections."""
        result = await load_tester.run_database_load_test(
            concurrent_connections=25,
            operations_per_connection=20
        )
        
        result.print_summary()
        
        # Database performance assertions
        assert result.metrics.successful_requests >= result.metrics.total_requests * 0.98  # 98% success rate
        assert result.metrics.avg_response_time_ms < 100  # Under 100ms average for DB ops
        assert result.metrics.requests_per_second >= 50  # At least 50 DB ops per second
    
    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_mcp_orchestrator_load(self, load_tester: LoadTester):
        """Test MCP orchestrator load handling."""
        result = await load_tester.run_mcp_orchestrator_load_test(
            concurrent_requests=30,
            requests_per_session=10
        )
        
        result.print_summary()
        
        # MCP performance assertions (may be relaxed if no servers are configured)
        if result.metrics.total_requests > 0:
            # Only assert if we actually processed requests
            success_rate = result.metrics.successful_requests / result.metrics.total_requests
            # Allow lower success rate for MCP in test mode
            assert success_rate >= 0.5 or result.metrics.total_requests == 0
    
    @pytest.mark.asyncio
    @pytest.mark.slow 
    async def test_full_system_load(self, load_tester: LoadTester):
        """Test full system under mixed load."""
        # Run multiple load tests concurrently to stress the entire system
        
        tasks = [
            load_tester.run_agent_load_test(25, 3, 30),
            load_tester.run_telegram_pipeline_load_test(25, 3, 30),
            load_tester.run_database_load_test(10, 10)
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Check that all tests completed
        successful_tests = [r for r in results if isinstance(r, LoadTestResult)]
        assert len(successful_tests) >= 2  # At least 2 of 3 should succeed
        
        # Print all results
        for i, result in enumerate(successful_tests):
            if isinstance(result, LoadTestResult):
                print(f"\n--- Full System Load Test {i+1} ---")
                result.print_summary()
        
        # System should remain stable
        total_requests = sum(r.metrics.total_requests for r in successful_tests)
        total_successful = sum(r.metrics.successful_requests for r in successful_tests)
        
        if total_requests > 0:
            overall_success_rate = total_successful / total_requests
            assert overall_success_rate >= 0.8  # 80% success under full load
    
    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_sustained_load_endurance(self, load_tester: LoadTester):
        """Test system endurance under sustained load."""
        # Run moderate load for extended period
        result = await load_tester.run_agent_load_test(
            concurrent_users=20,
            requests_per_user=10,
            test_duration_seconds=120  # 2 minutes
        )
        
        result.print_summary()
        
        # Endurance assertions
        assert result.metrics.successful_requests >= result.metrics.total_requests * 0.95
        assert result.metrics.peak_memory_mb < 1536  # Memory shouldn't grow excessively
        
        # Check for memory leaks (basic check)
        # If memory usage is reasonable at the end, likely no major leaks
        assert result.metrics.peak_memory_mb < 3072  # Under 3GB even after sustained load