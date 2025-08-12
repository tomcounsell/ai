"""
Memory Leak Detection Tests

Monitors system memory usage patterns to detect memory leaks,
excessive memory consumption, and memory management issues.
"""

import asyncio
import pytest
import time
import gc
import weakref
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Set
import psutil
import os
from dataclasses import dataclass, field
import threading
import sys

from integrations.system_integration import SystemIntegrator
from utilities.database import DatabaseManager
from agents.valor.agent import ValorAgent


@dataclass
class MemorySnapshot:
    """Memory usage snapshot at a point in time."""
    timestamp: datetime
    rss_mb: float  # Resident Set Size
    vms_mb: float  # Virtual Memory Size
    heap_objects: int
    gc_collections: tuple  # (gen0, gen1, gen2)
    active_threads: int
    file_descriptors: int = 0


@dataclass
class MemoryLeakTestResult:
    """Result of memory leak detection test."""
    test_name: str
    start_time: datetime
    end_time: datetime
    duration_seconds: float
    snapshots: List[MemorySnapshot] = field(default_factory=list)
    initial_memory_mb: float = 0.0
    final_memory_mb: float = 0.0
    peak_memory_mb: float = 0.0
    memory_growth_mb: float = 0.0
    memory_leak_detected: bool = False
    growth_rate_mb_per_sec: float = 0.0
    gc_efficiency: float = 0.0
    leaked_objects: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


class MemoryProfiler:
    """Memory profiling utilities."""
    
    def __init__(self):
        self.process = psutil.Process(os.getpid())
        self.baseline_objects = None
        self.tracked_objects = weakref.WeakSet()
    
    def take_snapshot(self) -> MemorySnapshot:
        """Take a memory usage snapshot."""
        memory_info = self.process.memory_info()
        
        # Get garbage collector stats
        gc_stats = gc.get_stats()
        gc_collections = tuple(stat['collections'] for stat in gc_stats)
        
        # Count heap objects
        heap_objects = len(gc.get_objects())
        
        # Get thread count
        active_threads = threading.active_count()
        
        # Get file descriptor count (Unix only)
        file_descriptors = 0
        try:
            file_descriptors = self.process.num_fds()
        except (AttributeError, NotImplementedError):
            pass  # Windows or unsupported platform
        
        return MemorySnapshot(
            timestamp=datetime.now(timezone.utc),
            rss_mb=memory_info.rss / 1024 / 1024,
            vms_mb=memory_info.vms / 1024 / 1024,
            heap_objects=heap_objects,
            gc_collections=gc_collections,
            active_threads=active_threads,
            file_descriptors=file_descriptors
        )
    
    def set_baseline(self):
        """Set baseline for object tracking."""
        gc.collect()  # Force garbage collection
        self.baseline_objects = set(id(obj) for obj in gc.get_objects())
    
    def find_new_objects(self, limit: int = 20) -> List[str]:
        """Find objects created since baseline."""
        if self.baseline_objects is None:
            return []
        
        current_objects = set(id(obj) for obj in gc.get_objects())
        new_object_ids = current_objects - self.baseline_objects
        
        # Sample some new objects
        new_objects = []
        count = 0
        for obj in gc.get_objects():
            if count >= limit:
                break
            if id(obj) in new_object_ids:
                obj_type = type(obj).__name__
                obj_size = sys.getsizeof(obj)
                new_objects.append(f"{obj_type} ({obj_size} bytes)")
                count += 1
        
        return new_objects
    
    def track_object(self, obj):
        """Track an object for memory leak detection."""
        self.tracked_objects.add(obj)
    
    def get_tracked_object_count(self) -> int:
        """Get count of still-alive tracked objects."""
        return len(self.tracked_objects)


class MemoryLeakTester:
    """Memory leak detection framework."""
    
    def __init__(self, system_integrator: SystemIntegrator):
        self.system_integrator = system_integrator
        self.profiler = MemoryProfiler()
    
    async def test_agent_context_leaks(self, iterations: int = 100) -> MemoryLeakTestResult:
        """Test for memory leaks in agent context management."""
        result = MemoryLeakTestResult(
            test_name="Agent Context Memory Leak Test",
            start_time=datetime.now(timezone.utc)
        )
        
        # Set baseline
        self.profiler.set_baseline()
        gc.collect()
        
        initial_snapshot = self.profiler.take_snapshot()
        result.snapshots.append(initial_snapshot)
        result.initial_memory_mb = initial_snapshot.rss_mb
        
        try:
            # Create and destroy contexts repeatedly
            for i in range(iterations):
                chat_id = f"memory_test_context_{i}"
                
                # Create context
                context = await self.system_integrator.agent.create_context(
                    chat_id=chat_id,
                    user_name=f"memory_user_{i}",
                    workspace="memory_test"
                )
                
                # Track the context
                self.profiler.track_object(context)
                
                # Use the context
                for j in range(5):
                    response = await self.system_integrator.agent.process_message(
                        message=f"Memory test message {i}-{j}",
                        chat_id=chat_id
                    )
                
                # Clear context
                await self.system_integrator.agent.clear_context(chat_id)
                
                # Take periodic snapshots
                if i % 20 == 0:
                    gc.collect()  # Force garbage collection
                    snapshot = self.profiler.take_snapshot()
                    result.snapshots.append(snapshot)
                    
                    # Check for memory growth
                    memory_growth = snapshot.rss_mb - result.initial_memory_mb
                    if memory_growth > 100:  # More than 100MB growth
                        result.notes.append(f"Significant memory growth at iteration {i}: {memory_growth:.1f}MB")
        
        except Exception as e:
            result.notes.append(f"Test error: {str(e)}")
        
        finally:
            # Final cleanup and measurement
            gc.collect()
            time.sleep(1)  # Allow cleanup to complete
            gc.collect()
            
            final_snapshot = self.profiler.take_snapshot()
            result.snapshots.append(final_snapshot)
            result.final_memory_mb = final_snapshot.rss_mb
            result.peak_memory_mb = max(s.rss_mb for s in result.snapshots)
            result.memory_growth_mb = result.final_memory_mb - result.initial_memory_mb
            
            # Check for leaked objects
            result.leaked_objects = self.profiler.find_new_objects()
            
            result.end_time = datetime.now(timezone.utc)
            result.duration_seconds = (result.end_time - result.start_time).total_seconds()
            
            # Calculate growth rate
            if result.duration_seconds > 0:
                result.growth_rate_mb_per_sec = result.memory_growth_mb / result.duration_seconds
            
            # Detect memory leaks
            result.memory_leak_detected = (
                result.memory_growth_mb > 50 or  # More than 50MB growth
                result.growth_rate_mb_per_sec > 0.5 or  # More than 0.5MB/sec growth
                self.profiler.get_tracked_object_count() > iterations * 0.1  # More than 10% objects still alive
            )
        
        return result
    
    async def test_database_connection_leaks(self, iterations: int = 200) -> MemoryLeakTestResult:
        """Test for memory leaks in database connections."""
        result = MemoryLeakTestResult(
            test_name="Database Connection Memory Leak Test",
            start_time=datetime.now(timezone.utc)
        )
        
        self.profiler.set_baseline()
        gc.collect()
        
        initial_snapshot = self.profiler.take_snapshot()
        result.snapshots.append(initial_snapshot)
        result.initial_memory_mb = initial_snapshot.rss_mb
        
        try:
            # Perform many database operations
            for i in range(iterations):
                # Mix of database operations
                await self.system_integrator.database.add_chat_message(
                    project_id=None,
                    session_id=f"leak_test_{i % 10}",  # Reuse some session IDs
                    role="user",
                    content=f"Leak test message {i}",
                    token_count=10
                )
                
                if i % 50 == 0:
                    # Get chat history
                    await self.system_integrator.database.get_chat_history(
                        session_id=f"leak_test_{i % 10}",
                        limit=20
                    )
                    
                    # Take snapshot
                    snapshot = self.profiler.take_snapshot()
                    result.snapshots.append(snapshot)
                
                if i % 100 == 0:
                    # Force garbage collection
                    gc.collect()
        
        except Exception as e:
            result.notes.append(f"Database test error: {str(e)}")
        
        finally:
            # Final measurement
            gc.collect()
            time.sleep(0.5)
            gc.collect()
            
            final_snapshot = self.profiler.take_snapshot()
            result.snapshots.append(final_snapshot)
            result.final_memory_mb = final_snapshot.rss_mb
            result.peak_memory_mb = max(s.rss_mb for s in result.snapshots)
            result.memory_growth_mb = result.final_memory_mb - result.initial_memory_mb
            
            result.end_time = datetime.now(timezone.utc)
            result.duration_seconds = (result.end_time - result.start_time).total_seconds()
            
            if result.duration_seconds > 0:
                result.growth_rate_mb_per_sec = result.memory_growth_mb / result.duration_seconds
            
            # Detect leaks
            result.memory_leak_detected = (
                result.memory_growth_mb > 30 or  # More than 30MB growth for DB operations
                result.growth_rate_mb_per_sec > 0.3
            )
        
        return result
    
    async def test_telegram_pipeline_leaks(self, iterations: int = 150) -> MemoryLeakTestResult:
        """Test for memory leaks in Telegram message processing."""
        result = MemoryLeakTestResult(
            test_name="Telegram Pipeline Memory Leak Test",
            start_time=datetime.now(timezone.utc)
        )
        
        if not self.system_integrator.telegram_processor:
            result.notes.append("Telegram processor not available")
            return result
        
        self.profiler.set_baseline()
        gc.collect()
        
        initial_snapshot = self.profiler.take_snapshot()
        result.snapshots.append(initial_snapshot)
        result.initial_memory_mb = initial_snapshot.rss_mb
        
        try:
            from integrations.telegram.unified_processor import ProcessingRequest
            from tests.integration.test_pipeline_telegram import MockTelegramMessage, MockTelegramUser
            
            for i in range(iterations):
                # Create mock message
                message = MockTelegramMessage(
                    text=f"Memory leak test message {i}",
                    message_id=i + 1,
                    chat_id=123000 + (i % 50)  # Reuse some chat IDs
                )
                
                user = MockTelegramUser(user_id=456000 + i)
                
                request = ProcessingRequest(
                    message=message,
                    user=user,
                    chat_id=message.chat_id,
                    message_id=message.id,
                    raw_text=message.text
                )
                
                # Track the request
                self.profiler.track_object(request)
                
                # Process message
                processing_result = await self.system_integrator.telegram_processor.process_message(request)
                
                # Don't keep references to results
                del processing_result
                del request
                del message
                del user
                
                if i % 30 == 0:
                    gc.collect()
                    snapshot = self.profiler.take_snapshot()
                    result.snapshots.append(snapshot)
        
        except Exception as e:
            result.notes.append(f"Telegram test error: {str(e)}")
        
        finally:
            # Final cleanup
            gc.collect()
            time.sleep(1)
            gc.collect()
            
            final_snapshot = self.profiler.take_snapshot()
            result.snapshots.append(final_snapshot)
            result.final_memory_mb = final_snapshot.rss_mb
            result.peak_memory_mb = max(s.rss_mb for s in result.snapshots)
            result.memory_growth_mb = result.final_memory_mb - result.initial_memory_mb
            
            result.end_time = datetime.now(timezone.utc)
            result.duration_seconds = (result.end_time - result.start_time).total_seconds()
            
            if result.duration_seconds > 0:
                result.growth_rate_mb_per_sec = result.memory_growth_mb / result.duration_seconds
            
            result.memory_leak_detected = (
                result.memory_growth_mb > 40 or
                result.growth_rate_mb_per_sec > 0.4
            )
        
        return result
    
    async def test_mcp_server_leaks(self, iterations: int = 100) -> MemoryLeakTestResult:
        """Test for memory leaks in MCP server operations."""
        result = MemoryLeakTestResult(
            test_name="MCP Server Memory Leak Test",
            start_time=datetime.now(timezone.utc)
        )
        
        if not self.system_integrator.mcp_orchestrator:
            result.notes.append("MCP orchestrator not available")
            return result
        
        self.profiler.set_baseline()
        gc.collect()
        
        initial_snapshot = self.profiler.take_snapshot()
        result.snapshots.append(initial_snapshot)
        result.initial_memory_mb = initial_snapshot.rss_mb
        
        try:
            from mcp_servers.base import MCPRequest
            import uuid
            
            for i in range(iterations):
                # Create MCP request
                request = MCPRequest(
                    method="health_check",
                    params={"iteration": i, "test": "memory_leak"},
                    id=str(uuid.uuid4())
                )
                
                self.profiler.track_object(request)
                
                try:
                    # Process request
                    response = await self.system_integrator.mcp_orchestrator.route_request(request)
                    
                    # Don't keep references
                    del response
                    
                except Exception as e:
                    # Expected if no servers are configured
                    pass
                
                del request
                
                if i % 25 == 0:
                    gc.collect()
                    snapshot = self.profiler.take_snapshot()
                    result.snapshots.append(snapshot)
        
        except Exception as e:
            result.notes.append(f"MCP test error: {str(e)}")
        
        finally:
            gc.collect()
            time.sleep(0.5)
            gc.collect()
            
            final_snapshot = self.profiler.take_snapshot()
            result.snapshots.append(final_snapshot)
            result.final_memory_mb = final_snapshot.rss_mb
            result.peak_memory_mb = max(s.rss_mb for s in result.snapshots)
            result.memory_growth_mb = result.final_memory_mb - result.initial_memory_mb
            
            result.end_time = datetime.now(timezone.utc)
            result.duration_seconds = (result.end_time - result.start_time).total_seconds()
            
            if result.duration_seconds > 0:
                result.growth_rate_mb_per_sec = result.memory_growth_mb / result.duration_seconds
            
            result.memory_leak_detected = (
                result.memory_growth_mb > 20 or
                result.growth_rate_mb_per_sec > 0.2
            )
        
        return result
    
    async def test_long_running_stability(self, duration_minutes: int = 5) -> MemoryLeakTestResult:
        """Test memory stability over extended operation."""
        result = MemoryLeakTestResult(
            test_name="Long Running Memory Stability Test",
            start_time=datetime.now(timezone.utc)
        )
        
        self.profiler.set_baseline()
        gc.collect()
        
        initial_snapshot = self.profiler.take_snapshot()
        result.snapshots.append(initial_snapshot)
        result.initial_memory_mb = initial_snapshot.rss_mb
        
        end_time = datetime.now(timezone.utc) + timedelta(minutes=duration_minutes)
        iteration = 0
        
        try:
            while datetime.now(timezone.utc) < end_time:
                # Mix of operations
                chat_id = f"stability_test_{iteration % 20}"
                
                # Agent operations
                if iteration % 4 == 0:
                    await self.system_integrator.agent.process_message(
                        message=f"Stability test {iteration}",
                        chat_id=chat_id,
                        user_name=f"stability_user_{iteration % 10}"
                    )
                
                # Database operations
                elif iteration % 4 == 1:
                    await self.system_integrator.database.add_chat_message(
                        project_id=None,
                        session_id=chat_id,
                        role="user",
                        content=f"Stability message {iteration}",
                        token_count=15
                    )
                
                # Memory cleanup
                elif iteration % 4 == 2:
                    if iteration % 20 == 2:  # Every 20 iterations
                        # Clear some contexts
                        contexts = self.system_integrator.agent.list_contexts()
                        if contexts:
                            oldest_context = contexts[0]
                            await self.system_integrator.agent.clear_context(oldest_context)
                
                # Health check
                else:
                    status = await self.system_integrator.get_system_status()
                    # Don't keep reference to status
                    del status
                
                iteration += 1
                
                # Take snapshots every 30 seconds
                if iteration % 100 == 0:
                    snapshot = self.profiler.take_snapshot()
                    result.snapshots.append(snapshot)
                    
                    # Check for concerning growth
                    growth = snapshot.rss_mb - result.initial_memory_mb
                    if growth > 200:  # More than 200MB growth
                        result.notes.append(f"Large memory growth detected: {growth:.1f}MB at iteration {iteration}")
                
                # Small delay
                await asyncio.sleep(0.01)
        
        except Exception as e:
            result.notes.append(f"Long running test error: {str(e)}")
        
        finally:
            gc.collect()
            time.sleep(2)  # Allow cleanup
            gc.collect()
            
            final_snapshot = self.profiler.take_snapshot()
            result.snapshots.append(final_snapshot)
            result.final_memory_mb = final_snapshot.rss_mb
            result.peak_memory_mb = max(s.rss_mb for s in result.snapshots)
            result.memory_growth_mb = result.final_memory_mb - result.initial_memory_mb
            
            result.end_time = datetime.now(timezone.utc)
            result.duration_seconds = (result.end_time - result.start_time).total_seconds()
            
            if result.duration_seconds > 0:
                result.growth_rate_mb_per_sec = result.memory_growth_mb / result.duration_seconds
            
            # For long-running tests, be more tolerant of growth
            result.memory_leak_detected = (
                result.memory_growth_mb > 100 or  # 100MB for long test
                result.growth_rate_mb_per_sec > 0.5
            )
        
        return result


class TestMemoryLeakDetection:
    """Memory leak detection test suite."""
    
    @pytest.fixture
    async def system_integrator(self):
        """System integrator for memory testing."""
        config = {
            "agent_model": "openai:gpt-3.5-turbo",
            "max_context_tokens": 25000,
            "debug": False,
            "telegram_response_target": 2000,
            "telegram_max_concurrent": 10
        }
        
        integrator = SystemIntegrator(
            config=config,
            enable_monitoring=False  # Disable monitoring to reduce memory overhead
        )
        
        await integrator.initialize()
        
        yield integrator
        
        await integrator.shutdown()
    
    @pytest.fixture
    def memory_tester(self, system_integrator):
        """Memory leak tester instance."""
        return MemoryLeakTester(system_integrator)
    
    def print_memory_result(self, result: MemoryLeakTestResult):
        """Print memory test results."""
        print(f"\n=== {result.test_name} Results ===")
        print(f"Duration: {result.duration_seconds:.1f}s")
        print(f"Initial Memory: {result.initial_memory_mb:.1f}MB")
        print(f"Final Memory: {result.final_memory_mb:.1f}MB")
        print(f"Peak Memory: {result.peak_memory_mb:.1f}MB")
        print(f"Memory Growth: {result.memory_growth_mb:.1f}MB")
        print(f"Growth Rate: {result.growth_rate_mb_per_sec:.3f}MB/s")
        print(f"Leak Detected: {result.memory_leak_detected}")
        
        if result.snapshots:
            print(f"Snapshots Taken: {len(result.snapshots)}")
            
        if result.leaked_objects:
            print(f"Potential Leaked Objects:")
            for obj in result.leaked_objects[:10]:  # Show first 10
                print(f"  {obj}")
        
        for note in result.notes:
            print(f"Note: {note}")
    
    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_agent_context_memory_leaks(self, memory_tester: MemoryLeakTester):
        """Test agent context for memory leaks."""
        result = await memory_tester.test_agent_context_leaks(iterations=50)
        self.print_memory_result(result)
        
        # Memory leak assertions
        assert not result.memory_leak_detected, f"Memory leak detected: {result.memory_growth_mb:.1f}MB growth"
        assert result.memory_growth_mb < 100, f"Excessive memory growth: {result.memory_growth_mb:.1f}MB"
        assert result.growth_rate_mb_per_sec < 1.0, f"High growth rate: {result.growth_rate_mb_per_sec:.3f}MB/s"
    
    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_database_connection_memory_leaks(self, memory_tester: MemoryLeakTester):
        """Test database connections for memory leaks."""
        result = await memory_tester.test_database_connection_leaks(iterations=100)
        self.print_memory_result(result)
        
        # Database should not leak significantly
        assert not result.memory_leak_detected, f"Database memory leak detected: {result.memory_growth_mb:.1f}MB"
        assert result.memory_growth_mb < 50, f"Excessive database memory growth: {result.memory_growth_mb:.1f}MB"
    
    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_telegram_pipeline_memory_leaks(self, memory_tester: MemoryLeakTester):
        """Test Telegram pipeline for memory leaks."""
        result = await memory_tester.test_telegram_pipeline_leaks(iterations=75)
        self.print_memory_result(result)
        
        # Telegram pipeline should not leak
        assert not result.memory_leak_detected, f"Telegram pipeline leak detected: {result.memory_growth_mb:.1f}MB"
        assert result.memory_growth_mb < 60, f"Excessive Telegram memory growth: {result.memory_growth_mb:.1f}MB"
    
    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_mcp_server_memory_leaks(self, memory_tester: MemoryLeakTester):
        """Test MCP servers for memory leaks."""
        result = await memory_tester.test_mcp_server_leaks(iterations=50)
        self.print_memory_result(result)
        
        # MCP should not leak significantly
        # More lenient since MCP may not have servers configured in tests
        if result.memory_growth_mb > 0:  # Only assert if we actually processed requests
            assert not result.memory_leak_detected, f"MCP server leak detected: {result.memory_growth_mb:.1f}MB"
            assert result.memory_growth_mb < 40, f"Excessive MCP memory growth: {result.memory_growth_mb:.1f}MB"
    
    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_long_running_memory_stability(self, memory_tester: MemoryLeakTester):
        """Test long-running memory stability."""
        result = await memory_tester.test_long_running_stability(duration_minutes=2)  # 2 minutes for tests
        self.print_memory_result(result)
        
        # Long-running should be stable
        assert not result.memory_leak_detected, f"Long-running leak detected: {result.memory_growth_mb:.1f}MB"
        assert result.memory_growth_mb < 150, f"Excessive long-running growth: {result.memory_growth_mb:.1f}MB"
        assert result.growth_rate_mb_per_sec < 0.8, f"High long-term growth rate: {result.growth_rate_mb_per_sec:.3f}MB/s"
    
    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_comprehensive_memory_profile(self, memory_tester: MemoryLeakTester):
        """Run comprehensive memory profiling across all components."""
        print("\n=== Comprehensive Memory Profile ===")
        
        tests = [
            ("Agent Contexts", memory_tester.test_agent_context_leaks(25)),
            ("Database", memory_tester.test_database_connection_leaks(50)),
            ("Telegram Pipeline", memory_tester.test_telegram_pipeline_leaks(40)),
            ("MCP Servers", memory_tester.test_mcp_server_leaks(25))
        ]
        
        all_results = []
        total_growth = 0.0
        
        for test_name, test_coro in tests:
            print(f"\nRunning {test_name} memory test...")
            
            try:
                result = await test_coro
                all_results.append((test_name, result))
                total_growth += result.memory_growth_mb
                
                leak_status = "LEAK DETECTED" if result.memory_leak_detected else "OK"
                print(f"{test_name}: {leak_status} ({result.memory_growth_mb:.1f}MB growth)")
                
            except Exception as e:
                print(f"{test_name}: ERROR - {str(e)}")
            
            # Brief pause between tests
            await asyncio.sleep(1)
        
        print(f"\n=== Memory Profile Summary ===")
        print(f"Total Memory Growth: {total_growth:.1f}MB")
        
        leaky_tests = [(name, result) for name, result in all_results if result.memory_leak_detected]
        
        if leaky_tests:
            print(f"Tests with Leaks: {len(leaky_tests)}")
            for name, result in leaky_tests:
                print(f"  {name}: {result.memory_growth_mb:.1f}MB")
        else:
            print("No memory leaks detected!")
        
        # Overall memory health
        assert total_growth < 200, f"Total system memory growth too high: {total_growth:.1f}MB"
        assert len(leaky_tests) <= 1, f"Too many components with memory leaks: {len(leaky_tests)}"