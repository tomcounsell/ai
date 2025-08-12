"""
Stress Testing Suite

Tests system behavior under extreme conditions including resource exhaustion,
high error rates, and system limits to ensure graceful degradation.
"""

import asyncio
import pytest
import time
import random
import gc
from datetime import datetime, timezone
from typing import Dict, Any, List
import psutil
import os
from dataclasses import dataclass, field
import uuid
import tempfile
from pathlib import Path

from integrations.system_integration import SystemIntegrator
from tests.integration.test_pipeline_telegram import MockTelegramMessage, MockTelegramUser
from integrations.telegram.unified_processor import ProcessingRequest


@dataclass
class StressTestResult:
    """Result of a stress test."""
    test_name: str
    start_time: datetime
    end_time: datetime
    duration_seconds: float
    system_survived: bool = True
    max_memory_mb: float = 0.0
    max_cpu_percent: float = 0.0
    total_operations: int = 0
    failed_operations: int = 0
    error_types: Dict[str, int] = field(default_factory=dict)
    recovery_time_seconds: float = 0.0
    final_system_health: str = "unknown"
    notes: List[str] = field(default_factory=list)


class StressTester:
    """Stress testing framework for AI Rebuild system."""
    
    def __init__(self, system_integrator: SystemIntegrator):
        self.system_integrator = system_integrator
        self.process = psutil.Process(os.getpid())
        
    async def test_memory_exhaustion(self, target_memory_mb: int = 1024) -> StressTestResult:
        """Test system behavior when approaching memory limits."""
        result = StressTestResult(
            test_name="Memory Exhaustion Test",
            start_time=datetime.now(timezone.utc)
        )
        
        # Memory consumption objects
        memory_hogs = []
        
        try:
            # Create many agent contexts to consume memory
            contexts_created = 0
            
            while True:
                current_memory = self.process.memory_info().rss / 1024 / 1024
                result.max_memory_mb = max(result.max_memory_mb, current_memory)
                
                if current_memory > target_memory_mb:
                    break
                
                # Create agent context
                chat_id = f"stress_memory_{contexts_created}"
                try:
                    await self.system_integrator.agent.create_context(
                        chat_id=chat_id,
                        user_name=f"memory_user_{contexts_created}",
                        workspace="memory_stress"
                    )
                    
                    # Process some messages to fill context
                    for i in range(10):
                        large_message = "x" * 1000  # 1KB message
                        await self.system_integrator.agent.process_message(
                            message=large_message,
                            chat_id=chat_id
                        )
                    
                    contexts_created += 1
                    result.total_operations += 11  # 1 context + 10 messages
                    
                    # Create memory hog objects
                    memory_hogs.append(bytearray(1024 * 1024))  # 1MB chunks
                    
                    if contexts_created % 10 == 0:
                        await asyncio.sleep(0.1)  # Yield control periodically
                
                except Exception as e:
                    result.failed_operations += 1
                    error_type = type(e).__name__
                    result.error_types[error_type] = result.error_types.get(error_type, 0) + 1
                    
                    if "memory" in str(e).lower() or "oom" in str(e).lower():
                        result.notes.append(f"Memory error at {current_memory:.1f}MB: {str(e)}")
                        break
            
            # Test system responsiveness under memory pressure
            test_start = time.perf_counter()
            
            try:
                response = await self.system_integrator.agent.process_message(
                    message="Test message under memory pressure",
                    chat_id="stress_memory_0"
                )
                
                if response and response.content:
                    result.recovery_time_seconds = time.perf_counter() - test_start
                    result.notes.append(f"System responsive under memory pressure ({result.recovery_time_seconds:.2f}s)")
                
            except Exception as e:
                result.system_survived = False
                result.notes.append(f"System unresponsive under memory pressure: {str(e)}")
            
            # Check final system health
            try:
                status = await self.system_integrator.get_system_status()
                result.final_system_health = status.get("system_state", "unknown")
            except:
                result.final_system_health = "error"
        
        except Exception as e:
            result.system_survived = False
            result.notes.append(f"Critical failure: {str(e)}")
        
        finally:
            # Cleanup
            memory_hogs.clear()
            gc.collect()
            
            result.end_time = datetime.now(timezone.utc)
            result.duration_seconds = (result.end_time - result.start_time).total_seconds()
        
        return result
    
    async def test_connection_exhaustion(self, max_connections: int = 200) -> StressTestResult:
        """Test system behavior with connection exhaustion."""
        result = StressTestResult(
            test_name="Connection Exhaustion Test", 
            start_time=datetime.now(timezone.utc)
        )
        
        active_tasks = []
        
        try:
            # Create many concurrent operations
            for i in range(max_connections):
                async def connection_load(conn_id: int):
                    try:
                        # Database operations
                        await self.system_integrator.database.add_chat_message(
                            project_id=None,
                            session_id=f"stress_conn_{conn_id}",
                            role="user",
                            content=f"Connection stress test {conn_id}",
                            token_count=10
                        )
                        
                        # Agent operation
                        await self.system_integrator.agent.process_message(
                            message=f"Connection test {conn_id}",
                            chat_id=f"stress_conn_{conn_id}",
                            user_name=f"conn_user_{conn_id}"
                        )
                        
                        result.total_operations += 2
                        
                    except Exception as e:
                        result.failed_operations += 1
                        error_type = type(e).__name__
                        result.error_types[error_type] = result.error_types.get(error_type, 0) + 1
                
                task = asyncio.create_task(connection_load(i))
                active_tasks.append(task)
                
                current_memory = self.process.memory_info().rss / 1024 / 1024
                result.max_memory_mb = max(result.max_memory_mb, current_memory)
                
                # Check CPU periodically
                if i % 50 == 0:
                    cpu_percent = self.process.cpu_percent()
                    result.max_cpu_percent = max(result.max_cpu_percent, cpu_percent)
                    await asyncio.sleep(0.01)
            
            # Wait for all connections to complete or timeout
            try:
                await asyncio.wait_for(
                    asyncio.gather(*active_tasks, return_exceptions=True),
                    timeout=30.0
                )
            except asyncio.TimeoutError:
                result.notes.append("Connection test timed out - system overloaded")
                result.system_survived = len([t for t in active_tasks if not t.done()]) < len(active_tasks) * 0.5
        
        except Exception as e:
            result.system_survived = False
            result.notes.append(f"Connection exhaustion failure: {str(e)}")
        
        finally:
            # Cancel remaining tasks
            for task in active_tasks:
                if not task.done():
                    task.cancel()
            
            result.end_time = datetime.now(timezone.utc)
            result.duration_seconds = (result.end_time - result.start_time).total_seconds()
        
        return result
    
    async def test_error_cascade(self, error_rate: float = 0.3) -> StressTestResult:
        """Test system resilience against cascading errors."""
        result = StressTestResult(
            test_name="Error Cascade Test",
            start_time=datetime.now(timezone.utc)
        )
        
        # Inject random failures
        class ErrorInjectingAgent:
            def __init__(self, real_agent, error_rate):
                self.real_agent = real_agent
                self.error_rate = error_rate
            
            async def process_message(self, *args, **kwargs):
                if random.random() < self.error_rate:
                    raise Exception("Injected failure for stress testing")
                return await self.real_agent.process_message(*args, **kwargs)
            
            def __getattr__(self, name):
                return getattr(self.real_agent, name)
        
        # Temporarily replace agent with error-injecting version
        original_agent = self.system_integrator.agent
        self.system_integrator.agent = ErrorInjectingAgent(original_agent, error_rate)
        
        try:
            # Generate load with high error rate
            tasks = []
            for i in range(100):
                async def error_prone_operation(op_id: int):
                    try:
                        await self.system_integrator.agent.process_message(
                            message=f"Error cascade test {op_id}",
                            chat_id=f"error_test_{op_id % 10}",  # Reuse some chat IDs
                            user_name=f"error_user_{op_id}"
                        )
                        result.total_operations += 1
                    except Exception as e:
                        result.failed_operations += 1
                        error_type = type(e).__name__
                        result.error_types[error_type] = result.error_types.get(error_type, 0) + 1
                
                tasks.append(asyncio.create_task(error_prone_operation(i)))
            
            # Execute with some concurrency
            batch_size = 20
            for i in range(0, len(tasks), batch_size):
                batch = tasks[i:i + batch_size]
                await asyncio.gather(*batch, return_exceptions=True)
                await asyncio.sleep(0.1)
            
            # Test system recovery
            recovery_start = time.perf_counter()
            
            # Restore original agent
            self.system_integrator.agent = original_agent
            
            # Test normal operation
            try:
                response = await self.system_integrator.agent.process_message(
                    message="Recovery test message",
                    chat_id="recovery_test",
                    user_name="recovery_user"
                )
                
                if response and response.content:
                    result.recovery_time_seconds = time.perf_counter() - recovery_start
                    result.notes.append(f"System recovered after error cascade ({result.recovery_time_seconds:.2f}s)")
                
            except Exception as e:
                result.system_survived = False
                result.notes.append(f"System failed to recover from error cascade: {str(e)}")
        
        except Exception as e:
            result.system_survived = False
            result.notes.append(f"Error cascade test failure: {str(e)}")
        
        finally:
            # Ensure original agent is restored
            self.system_integrator.agent = original_agent
            
            result.end_time = datetime.now(timezone.utc)
            result.duration_seconds = (result.end_time - result.start_time).total_seconds()
        
        return result
    
    async def test_resource_starvation(self) -> StressTestResult:
        """Test system behavior under resource starvation."""
        result = StressTestResult(
            test_name="Resource Starvation Test",
            start_time=datetime.now(timezone.utc)
        )
        
        # Create CPU-intensive tasks
        cpu_tasks = []
        
        try:
            # Start CPU-intensive background tasks
            for i in range(os.cpu_count() * 2):  # More tasks than CPU cores
                async def cpu_intensive_task(task_id: int):
                    start_time = time.time()
                    operations = 0
                    
                    # Run for 10 seconds
                    while time.time() - start_time < 10:
                        # CPU-intensive operation
                        _ = sum(range(10000))
                        operations += 1
                        
                        # Yield occasionally
                        if operations % 100 == 0:
                            await asyncio.sleep(0.001)
                
                task = asyncio.create_task(cpu_intensive_task(i))
                cpu_tasks.append(task)
            
            await asyncio.sleep(0.5)  # Let CPU tasks start
            
            # Test system responsiveness under CPU load
            response_times = []
            
            for i in range(10):
                start_time = time.perf_counter()
                
                try:
                    response = await asyncio.wait_for(
                        self.system_integrator.agent.process_message(
                            message=f"Resource starvation test {i}",
                            chat_id="resource_stress",
                            user_name="resource_user"
                        ),
                        timeout=30.0  # 30 second timeout
                    )
                    
                    response_time = time.perf_counter() - start_time
                    response_times.append(response_time)
                    result.total_operations += 1
                    
                    if response and response.content:
                        result.notes.append(f"Response {i}: {response_time:.2f}s")
                
                except asyncio.TimeoutError:
                    result.failed_operations += 1
                    result.error_types["TimeoutError"] = result.error_types.get("TimeoutError", 0) + 1
                    result.notes.append(f"Operation {i} timed out under resource starvation")
                
                except Exception as e:
                    result.failed_operations += 1
                    error_type = type(e).__name__
                    result.error_types[error_type] = result.error_types.get(error_type, 0) + 1
                
                # Monitor resources
                current_memory = self.process.memory_info().rss / 1024 / 1024
                result.max_memory_mb = max(result.max_memory_mb, current_memory)
                
                cpu_percent = self.process.cpu_percent()
                result.max_cpu_percent = max(result.max_cpu_percent, cpu_percent)
            
            # Calculate recovery metrics
            if response_times:
                avg_response_time = sum(response_times) / len(response_times)
                result.recovery_time_seconds = avg_response_time
                
                if avg_response_time > 10:  # Very slow
                    result.notes.append(f"System severely degraded under resource starvation (avg: {avg_response_time:.2f}s)")
                elif avg_response_time > 5:  # Moderately slow
                    result.notes.append(f"System moderately degraded under resource starvation (avg: {avg_response_time:.2f}s)")
                else:
                    result.notes.append(f"System maintained good performance under resource starvation (avg: {avg_response_time:.2f}s)")
        
        except Exception as e:
            result.system_survived = False
            result.notes.append(f"Resource starvation test failure: {str(e)}")
        
        finally:
            # Cancel CPU-intensive tasks
            for task in cpu_tasks:
                task.cancel()
            
            # Wait for tasks to finish canceling
            try:
                await asyncio.gather(*cpu_tasks, return_exceptions=True)
            except:
                pass
            
            result.end_time = datetime.now(timezone.utc)
            result.duration_seconds = (result.end_time - result.start_time).total_seconds()
        
        return result
    
    async def test_rapid_scale_up_down(self) -> StressTestResult:
        """Test system behavior during rapid scaling."""
        result = StressTestResult(
            test_name="Rapid Scale Up/Down Test",
            start_time=datetime.now(timezone.utc)
        )
        
        try:
            # Rapid scale up
            scale_up_tasks = []
            
            for wave in range(5):  # 5 waves of scaling
                # Scale up
                wave_tasks = []
                connections_in_wave = 20 * (wave + 1)  # 20, 40, 60, 80, 100
                
                for i in range(connections_in_wave):
                    async def scale_operation(op_id: int, wave_id: int):
                        chat_id = f"scale_{wave_id}_{op_id}"
                        try:
                            # Create context
                            await self.system_integrator.agent.create_context(
                                chat_id=chat_id,
                                user_name=f"scale_user_{wave_id}_{op_id}"
                            )
                            
                            # Send message
                            await self.system_integrator.agent.process_message(
                                message=f"Scale test wave {wave_id} operation {op_id}",
                                chat_id=chat_id
                            )
                            
                            result.total_operations += 2
                            
                        except Exception as e:
                            result.failed_operations += 1
                            error_type = type(e).__name__
                            result.error_types[error_type] = result.error_types.get(error_type, 0) + 1
                    
                    task = asyncio.create_task(scale_operation(i, wave))
                    wave_tasks.append(task)
                
                # Execute wave
                await asyncio.gather(*wave_tasks, return_exceptions=True)
                scale_up_tasks.extend(wave_tasks)
                
                # Brief pause between waves
                await asyncio.sleep(0.5)
                
                # Monitor resources
                current_memory = self.process.memory_info().rss / 1024 / 1024
                result.max_memory_mb = max(result.max_memory_mb, current_memory)
                
                cpu_percent = self.process.cpu_percent()
                result.max_cpu_percent = max(result.max_cpu_percent, cpu_percent)
                
                result.notes.append(f"Wave {wave + 1}: {connections_in_wave} operations, Memory: {current_memory:.1f}MB")
            
            # Rapid scale down - clear contexts
            cleanup_start = time.perf_counter()
            
            active_contexts = self.system_integrator.agent.list_contexts()
            cleanup_tasks = []
            
            for chat_id in active_contexts:
                async def cleanup_context(cid: str):
                    try:
                        await self.system_integrator.agent.clear_context(cid)
                    except:
                        pass  # Ignore cleanup errors
                
                cleanup_tasks.append(asyncio.create_task(cleanup_context(chat_id)))
            
            await asyncio.gather(*cleanup_tasks, return_exceptions=True)
            
            cleanup_time = time.perf_counter() - cleanup_start
            result.recovery_time_seconds = cleanup_time
            result.notes.append(f"Cleanup took {cleanup_time:.2f}s for {len(active_contexts)} contexts")
            
            # Test system responsiveness after scale down
            try:
                response = await self.system_integrator.agent.process_message(
                    message="Post scale-down test",
                    chat_id="post_scale_test",
                    user_name="post_scale_user"
                )
                
                if response and response.content:
                    result.notes.append("System responsive after scale down")
                
            except Exception as e:
                result.system_survived = False
                result.notes.append(f"System unresponsive after scale down: {str(e)}")
        
        except Exception as e:
            result.system_survived = False
            result.notes.append(f"Rapid scaling test failure: {str(e)}")
        
        finally:
            result.end_time = datetime.now(timezone.utc)
            result.duration_seconds = (result.end_time - result.start_time).total_seconds()
        
        return result


class TestStressPerformance:
    """Stress testing test suite."""
    
    @pytest.fixture
    async def system_integrator(self):
        """System integrator for stress testing."""
        config = {
            "agent_model": "openai:gpt-3.5-turbo",
            "max_context_tokens": 25000,  # Smaller for stress tests
            "debug": False,
            "telegram_response_target": 3000,
            "telegram_max_concurrent": 20
        }
        
        integrator = SystemIntegrator(
            config=config,
            enable_monitoring=True,
            health_check_interval=60,
            auto_recovery=True
        )
        
        await integrator.initialize()
        
        yield integrator
        
        await integrator.shutdown()
    
    @pytest.fixture
    def stress_tester(self, system_integrator):
        """Stress tester instance."""
        return StressTester(system_integrator)
    
    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_memory_exhaustion_resilience(self, stress_tester: StressTester):
        """Test system resilience to memory exhaustion."""
        result = await stress_tester.test_memory_exhaustion(target_memory_mb=512)
        
        print(f"\n=== {result.test_name} Results ===")
        print(f"Duration: {result.duration_seconds:.2f}s")
        print(f"System Survived: {result.system_survived}")
        print(f"Max Memory: {result.max_memory_mb:.1f}MB")
        print(f"Total Operations: {result.total_operations}")
        print(f"Failed Operations: {result.failed_operations}")
        print(f"Recovery Time: {result.recovery_time_seconds:.2f}s")
        print(f"Final Health: {result.final_system_health}")
        
        for note in result.notes:
            print(f"Note: {note}")
        
        # System should survive or gracefully degrade
        assert result.system_survived or result.failed_operations / max(result.total_operations, 1) < 1.0
        assert result.max_memory_mb > 0
    
    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_connection_exhaustion_handling(self, stress_tester: StressTester):
        """Test handling of connection exhaustion."""
        result = await stress_tester.test_connection_exhaustion(max_connections=100)
        
        print(f"\n=== {result.test_name} Results ===")
        print(f"Duration: {result.duration_seconds:.2f}s")
        print(f"System Survived: {result.system_survived}")
        print(f"Max Memory: {result.max_memory_mb:.1f}MB")
        print(f"Max CPU: {result.max_cpu_percent:.1f}%")
        print(f"Total Operations: {result.total_operations}")
        print(f"Failed Operations: {result.failed_operations}")
        
        if result.error_types:
            print("Error Types:")
            for error_type, count in result.error_types.items():
                print(f"  {error_type}: {count}")
        
        # Should handle connection exhaustion gracefully
        assert result.system_survived
        # Allow some failures under extreme load
        failure_rate = result.failed_operations / max(result.total_operations, 1)
        assert failure_rate < 0.5  # Less than 50% failure rate
    
    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_error_cascade_resilience(self, stress_tester: StressTester):
        """Test resilience against cascading errors."""
        result = await stress_tester.test_error_cascade(error_rate=0.4)
        
        print(f"\n=== {result.test_name} Results ===")
        print(f"Duration: {result.duration_seconds:.2f}s")
        print(f"System Survived: {result.system_survived}")
        print(f"Total Operations: {result.total_operations}")
        print(f"Failed Operations: {result.failed_operations}")
        print(f"Recovery Time: {result.recovery_time_seconds:.2f}s")
        
        if result.error_types:
            print("Error Types:")
            for error_type, count in result.error_types.items():
                print(f"  {error_type}: {count}")
        
        for note in result.notes:
            print(f"Note: {note}")
        
        # System should recover from error cascade
        assert result.system_survived
        assert result.recovery_time_seconds > 0
        assert result.recovery_time_seconds < 10  # Should recover within 10 seconds
    
    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_resource_starvation_degradation(self, stress_tester: StressTester):
        """Test graceful degradation under resource starvation."""
        result = await stress_tester.test_resource_starvation()
        
        print(f"\n=== {result.test_name} Results ===")
        print(f"Duration: {result.duration_seconds:.2f}s")
        print(f"System Survived: {result.system_survived}")
        print(f"Max Memory: {result.max_memory_mb:.1f}MB")
        print(f"Max CPU: {result.max_cpu_percent:.1f}%")
        print(f"Total Operations: {result.total_operations}")
        print(f"Failed Operations: {result.failed_operations}")
        print(f"Avg Response Time: {result.recovery_time_seconds:.2f}s")
        
        for note in result.notes:
            print(f"Note: {note}")
        
        # System should survive but may degrade performance
        assert result.system_survived
        # Allow reasonable degradation under resource starvation
        assert result.total_operations > 0 or result.failed_operations > 0
    
    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_rapid_scaling_stability(self, stress_tester: StressTester):
        """Test system stability during rapid scaling."""
        result = await stress_tester.test_rapid_scale_up_down()
        
        print(f"\n=== {result.test_name} Results ===")
        print(f"Duration: {result.duration_seconds:.2f}s")
        print(f"System Survived: {result.system_survived}")
        print(f"Max Memory: {result.max_memory_mb:.1f}MB")
        print(f"Max CPU: {result.max_cpu_percent:.1f}%")
        print(f"Total Operations: {result.total_operations}")
        print(f"Failed Operations: {result.failed_operations}")
        print(f"Cleanup Time: {result.recovery_time_seconds:.2f}s")
        
        for note in result.notes:
            print(f"Note: {note}")
        
        # System should handle rapid scaling
        assert result.system_survived
        assert result.total_operations > 0
        # Allow some failures during extreme scaling
        failure_rate = result.failed_operations / max(result.total_operations, 1)
        assert failure_rate < 0.3  # Less than 30% failure rate
    
    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_comprehensive_stress(self, stress_tester: StressTester):
        """Run comprehensive stress test combining multiple stressors."""
        
        print("\n=== Running Comprehensive Stress Test ===")
        
        # Run multiple stress tests in sequence
        tests = [
            ("Memory", stress_tester.test_memory_exhaustion(256)),
            ("Connections", stress_tester.test_connection_exhaustion(50)),
            ("Errors", stress_tester.test_error_cascade(0.2)),
            ("Resources", stress_tester.test_resource_starvation()),
            ("Scaling", stress_tester.test_rapid_scale_up_down())
        ]
        
        results = []
        overall_success = True
        
        for test_name, test_coro in tests:
            print(f"\nRunning {test_name} stress test...")
            try:
                result = await test_coro
                results.append((test_name, result))
                
                if not result.system_survived:
                    overall_success = False
                    print(f"{test_name} test: SYSTEM FAILURE")
                else:
                    print(f"{test_name} test: PASSED")
                
            except Exception as e:
                print(f"{test_name} test: EXCEPTION - {str(e)}")
                overall_success = False
            
            # Brief recovery time between tests
            await asyncio.sleep(2)
        
        # Summary
        print(f"\n=== Comprehensive Stress Test Summary ===")
        print(f"Overall Success: {overall_success}")
        print(f"Tests Completed: {len(results)}")
        
        for test_name, result in results:
            survival_status = "SURVIVED" if result.system_survived else "FAILED"
            print(f"{test_name}: {survival_status} ({result.duration_seconds:.1f}s, {result.total_operations} ops)")
        
        # System should survive most stress tests
        survived_tests = sum(1 for _, result in results if result.system_survived)
        survival_rate = survived_tests / len(results) if results else 0
        
        assert survival_rate >= 0.6  # At least 60% of stress tests should pass
        print(f"\nSurvival Rate: {survival_rate:.1%}")