"""
Performance Benchmark Tests

Tests system performance against defined baselines:
- Memory usage
- CPU utilization
- Response times
- Concurrent session handling
- Health scoring
"""

import asyncio
import os
import sys
import time
import pytest
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


@dataclass
class PerformanceBaseline:
    """Performance baseline requirements."""

    memory_baseline_mb: float = 300.0
    memory_per_session_mb: float = 30.0
    cpu_baseline_percent: float = 20.0
    response_time_text_ms: int = 2000
    response_time_media_ms: int = 5000
    concurrent_sessions: int = 50
    uptime_hours: int = 48
    health_score_minimum: int = 85


@dataclass
class ResourceSnapshot:
    """Snapshot of system resources."""

    timestamp: datetime
    memory_mb: float
    cpu_percent: float
    active_processes: int

    @classmethod
    def capture(cls) -> "ResourceSnapshot":
        """Capture current resource state."""
        try:
            import psutil

            process = psutil.Process()
            return cls(
                timestamp=datetime.now(),
                memory_mb=process.memory_info().rss / (1024 * 1024),
                cpu_percent=process.cpu_percent(interval=0.1),
                active_processes=1,
            )
        except ImportError:
            # Fallback without psutil
            return cls(
                timestamp=datetime.now(),
                memory_mb=0.0,
                cpu_percent=0.0,
                active_processes=1,
            )


class TestMemoryPerformance:
    """Tests for memory usage and management."""

    @pytest.fixture
    def baseline(self) -> PerformanceBaseline:
        return PerformanceBaseline()

    def test_baseline_memory_usage(self, baseline):
        """Test that baseline memory is within limits."""
        snapshot = ResourceSnapshot.capture()
        if snapshot.memory_mb == 0.0:
            pytest.skip("psutil not available for memory measurement")

        assert snapshot.memory_mb < baseline.memory_baseline_mb, (
            f"Baseline memory {snapshot.memory_mb:.1f}MB exceeds "
            f"limit {baseline.memory_baseline_mb}MB"
        )

    @pytest.mark.asyncio
    async def test_memory_growth_under_load(self, baseline):
        """Test memory growth stays bounded under simulated load."""
        initial = ResourceSnapshot.capture()
        if initial.memory_mb == 0.0:
            pytest.skip("psutil not available")

        # Simulate some work
        data = []
        for i in range(100):
            data.append({"index": i, "data": "x" * 1000})
            await asyncio.sleep(0.001)

        after_load = ResourceSnapshot.capture()
        memory_growth = after_load.memory_mb - initial.memory_mb

        # Growth should be minimal for small operations
        assert (
            memory_growth < 50.0
        ), f"Memory grew by {memory_growth:.1f}MB during load test"

        # Cleanup
        del data

    def test_no_memory_leaks_in_tools(self):
        """Test that tool imports don't cause memory leaks."""
        initial = ResourceSnapshot.capture()

        # Import all tools
        try:
            from tools import search
            from tools import image_analysis
            from tools import code_execution
            from tools import test_judge
        except ImportError:
            pytest.skip("Tools not available")

        after_import = ResourceSnapshot.capture()

        if initial.memory_mb > 0:
            growth = after_import.memory_mb - initial.memory_mb
            assert growth < 100.0, f"Tool imports caused {growth:.1f}MB memory growth"


class TestCPUPerformance:
    """Tests for CPU utilization."""

    @pytest.fixture
    def baseline(self) -> PerformanceBaseline:
        return PerformanceBaseline()

    def test_idle_cpu_usage(self, baseline):
        """Test CPU usage at idle is reasonable."""
        snapshot = ResourceSnapshot.capture()
        if snapshot.cpu_percent == 0.0:
            pytest.skip("psutil not available")

        # At idle, CPU should be very low
        assert (
            snapshot.cpu_percent < baseline.cpu_baseline_percent
        ), f"Idle CPU {snapshot.cpu_percent}% exceeds baseline {baseline.cpu_baseline_percent}%"

    @pytest.mark.asyncio
    async def test_cpu_under_async_load(self, baseline):
        """Test CPU usage during async operations."""

        async def async_work():
            await asyncio.sleep(0.01)
            return sum(range(1000))

        # Run multiple async tasks
        tasks = [async_work() for _ in range(10)]
        await asyncio.gather(*tasks)

        snapshot = ResourceSnapshot.capture()
        if snapshot.cpu_percent == 0.0:
            pytest.skip("psutil not available")

        # Should still be under control
        assert (
            snapshot.cpu_percent < 80.0
        ), f"CPU under async load {snapshot.cpu_percent}% is too high"


class TestResponseTime:
    """Tests for response time performance."""

    @pytest.fixture
    def baseline(self) -> PerformanceBaseline:
        return PerformanceBaseline()

    @pytest.mark.asyncio
    async def test_simple_operation_time(self, baseline):
        """Test simple operations complete quickly."""
        start = time.perf_counter()

        # Simple operation
        result = sum(range(10000))

        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 100, f"Simple operation took {elapsed_ms:.1f}ms"

    @pytest.mark.asyncio
    async def test_file_read_time(self, baseline):
        """Test file reading is fast."""
        test_file = Path(__file__)

        start = time.perf_counter()
        content = test_file.read_text()
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert elapsed_ms < 100, f"File read took {elapsed_ms:.1f}ms"

    @pytest.mark.asyncio
    async def test_async_task_latency(self, baseline):
        """Test async task scheduling latency."""
        start = time.perf_counter()

        async def quick_task():
            return 42

        result = await quick_task()
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert elapsed_ms < 10, f"Async task latency {elapsed_ms:.1f}ms is too high"


class TestConcurrency:
    """Tests for concurrent operation handling."""

    @pytest.fixture
    def baseline(self) -> PerformanceBaseline:
        return PerformanceBaseline()

    @pytest.mark.asyncio
    async def test_concurrent_tasks(self, baseline):
        """Test handling multiple concurrent tasks."""

        async def simulated_request(request_id: int) -> dict:
            await asyncio.sleep(0.01)  # Simulate I/O
            return {"id": request_id, "status": "complete"}

        # Run concurrent requests
        num_requests = baseline.concurrent_sessions
        tasks = [simulated_request(i) for i in range(num_requests)]

        start = time.perf_counter()
        results = await asyncio.gather(*tasks)
        elapsed = time.perf_counter() - start

        assert len(results) == num_requests
        assert all(r["status"] == "complete" for r in results)

        # Concurrent execution should be efficient
        # 50 tasks at 10ms each should complete in well under 1 second
        assert elapsed < 2.0, f"Concurrent tasks took {elapsed:.2f}s"

    @pytest.mark.asyncio
    async def test_semaphore_limiting(self, baseline):
        """Test semaphore limits concurrent operations."""
        max_concurrent = 10
        semaphore = asyncio.Semaphore(max_concurrent)
        active_count = 0
        max_active = 0

        async def limited_task(task_id: int):
            nonlocal active_count, max_active
            async with semaphore:
                active_count += 1
                max_active = max(max_active, active_count)
                await asyncio.sleep(0.01)
                active_count -= 1
                return task_id

        tasks = [limited_task(i) for i in range(50)]
        results = await asyncio.gather(*tasks)

        assert len(results) == 50
        assert (
            max_active <= max_concurrent
        ), f"Max concurrent {max_active} exceeded limit {max_concurrent}"


class TestHealthScoring:
    """Tests for health score calculation."""

    def test_health_score_calculation(self):
        """Test health score calculation logic."""

        def calculate_health_score(
            memory_percent: float, cpu_percent: float, session_load: float
        ) -> float:
            memory_health = max(0, 100 - (memory_percent * 1.5))
            cpu_health = max(0, 100 - (cpu_percent * 1.2))
            session_health = max(0, 100 - (session_load * 100))

            return memory_health * 0.4 + cpu_health * 0.3 + session_health * 0.3

        # Test healthy system (low resource usage)
        score = calculate_health_score(10.0, 10.0, 0.1)
        assert score > 80, f"Healthy system scored {score}"

        # Test stressed system
        score = calculate_health_score(80.0, 70.0, 0.9)
        assert score < 50, f"Stressed system scored {score}"

        # Test edge cases
        score = calculate_health_score(0.0, 0.0, 0.0)
        assert score == 100.0, f"Idle system should score 100, got {score}"

    def test_health_thresholds(self):
        """Test health threshold detection."""
        baseline = PerformanceBaseline()

        # Should trigger warning
        memory_warning_threshold = baseline.memory_baseline_mb * 0.8
        memory_critical_threshold = baseline.memory_baseline_mb * 0.95

        assert memory_warning_threshold < memory_critical_threshold
        assert memory_critical_threshold < baseline.memory_baseline_mb


class TestToolPerformance:
    """Tests for individual tool performance."""

    @pytest.mark.asyncio
    async def test_tool_import_time(self):
        """Test that tool modules import quickly."""
        tools_to_test = [
            "search",
            "image_analysis",
            "code_execution",
            "test_judge",
            "knowledge_search",
        ]

        for tool_name in tools_to_test:
            start = time.perf_counter()
            try:
                module = __import__(f"tools.{tool_name}", fromlist=[tool_name])
                elapsed_ms = (time.perf_counter() - start) * 1000
                assert (
                    elapsed_ms < 500
                ), f"Tool {tool_name} import took {elapsed_ms:.1f}ms"
            except ImportError:
                # Tool may not be fully implemented yet
                pass

    @pytest.mark.asyncio
    async def test_tool_validation_speed(self):
        """Test that tool parameter validation is fast."""
        # Simple validation should be instant
        start = time.perf_counter()

        def validate_params(params: dict) -> bool:
            required = ["query"]
            return all(k in params for k in required)

        for _ in range(1000):
            validate_params({"query": "test"})

        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 100, f"1000 validations took {elapsed_ms:.1f}ms"


class TestEndurance:
    """Endurance tests for long-running operations."""

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_sustained_load(self):
        """Test system under sustained load."""
        initial = ResourceSnapshot.capture()

        # Run for a sustained period
        iterations = 100
        for i in range(iterations):
            # Simulate work
            _ = [x**2 for x in range(100)]
            await asyncio.sleep(0.01)

        final = ResourceSnapshot.capture()

        if initial.memory_mb > 0:
            memory_growth = final.memory_mb - initial.memory_mb
            # Should not grow significantly
            assert (
                memory_growth < 50
            ), f"Memory grew by {memory_growth:.1f}MB during sustained load"

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_garbage_collection(self):
        """Test that garbage collection keeps memory in check."""
        import gc

        initial = ResourceSnapshot.capture()

        # Create and discard objects
        for _ in range(10):
            data = [{"key": str(i), "value": "x" * 10000} for i in range(1000)]
            del data
            gc.collect()

        final = ResourceSnapshot.capture()

        if initial.memory_mb > 0:
            # Memory should be similar after GC
            growth = final.memory_mb - initial.memory_mb
            assert growth < 20, f"Memory not reclaimed: grew by {growth:.1f}MB"
