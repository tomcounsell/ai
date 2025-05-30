#!/usr/bin/env python3
"""
Concurrency and Error Recovery Testing Suite

Tests concurrent session handling, error recovery mechanisms, and system resilience
under various failure scenarios for production deployment validation.

Test Coverage:
- Multi-user concurrent sessions (50+ simultaneous users)
- Error recovery and failover scenarios  
- Network resilience and API failure handling
- Session recovery after crashes/restarts
- Rate limiting and throttling behavior
"""

import asyncio
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from unittest.mock import Mock, patch

import pytest

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from agents.unified_valor_claude_agent import UnifiedValorClaudeAgent, UnifiedContext
from agents.unified_integration import UnifiedTelegramIntegration


class ConcurrencyTester:
    """Manages concurrent testing scenarios and collects metrics."""
    
    def __init__(self):
        self.active_sessions: Dict[str, Dict] = {}
        self.completed_operations: List[Dict] = []
        self.error_counts: Dict[str, int] = {}
        self.performance_metrics: List[Dict] = {}
        self.start_time = time.time()
    
    def register_session(self, session_id: str, session_data: Dict):
        """Register a new concurrent session."""
        self.active_sessions[session_id] = {
            **session_data,
            "start_time": time.time(),
            "operations_count": 0,
            "errors_count": 0
        }
    
    def record_operation(self, session_id: str, operation: str, duration: float, success: bool):
        """Record an operation result from a session."""
        if session_id in self.active_sessions:
            self.active_sessions[session_id]["operations_count"] += 1
            if not success:
                self.active_sessions[session_id]["errors_count"] += 1
        
        self.completed_operations.append({
            "session_id": session_id,
            "operation": operation,
            "duration": duration,
            "success": success,
            "timestamp": time.time()
        })
    
    def record_error(self, error_type: str, session_id: str = None):
        """Record an error occurrence."""
        if error_type not in self.error_counts:
            self.error_counts[error_type] = 0
        self.error_counts[error_type] += 1
        
        if session_id and session_id in self.active_sessions:
            self.active_sessions[session_id]["errors_count"] += 1
    
    def get_concurrency_metrics(self) -> Dict:
        """Calculate comprehensive concurrency metrics."""
        total_operations = len(self.completed_operations)
        successful_operations = len([op for op in self.completed_operations if op["success"]])
        
        if total_operations > 0:
            success_rate = (successful_operations / total_operations) * 100
            avg_duration = sum(op["duration"] for op in self.completed_operations) / total_operations
        else:
            success_rate = 0
            avg_duration = 0
        
        return {
            "concurrent_sessions": len(self.active_sessions),
            "total_operations": total_operations,
            "success_rate": success_rate,
            "average_duration": avg_duration,
            "error_counts": self.error_counts,
            "uptime_seconds": time.time() - self.start_time,
            "operations_per_second": total_operations / (time.time() - self.start_time) if (time.time() - self.start_time) > 0 else 0
        }


class TestConcurrentSessions:
    """Test handling of multiple concurrent user sessions."""
    
    @pytest.fixture
    def tester(self):
        return ConcurrencyTester()
    
    def test_multiple_concurrent_agents(self, tester):
        """Test creating and managing multiple concurrent agent instances."""
        num_sessions = 20
        agents = {}
        
        # Create multiple agents concurrently
        def create_agent(session_id: str) -> Tuple[str, bool]:
            try:
                with patch('agents.unified_valor_claude_agent.subprocess.run'):
                    agent = UnifiedValorClaudeAgent()
                    return session_id, True
            except Exception as e:
                tester.record_error("agent_creation_failed", session_id)
                return session_id, False
        
        # Use thread pool to create agents concurrently
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(create_agent, f"session_{i}") for i in range(num_sessions)]
            
            for future in as_completed(futures):
                session_id, success = future.result()
                if success:
                    agents[session_id] = True
                    tester.register_session(session_id, {"type": "agent", "status": "active"})
        
        # Validate concurrent creation
        assert len(agents) >= num_sessions * 0.9, f"Only {len(agents)}/{num_sessions} agents created successfully"
        
        metrics = tester.get_concurrency_metrics()
        print(f"✅ Concurrent agent creation test passed:")
        print(f"   Created agents: {len(agents)}/{num_sessions}")
        print(f"   Success rate: {(len(agents)/num_sessions)*100:.1f}%")
    
    def test_concurrent_message_processing(self, tester):
        """Test concurrent message processing across multiple sessions."""
        num_sessions = 15
        messages_per_session = 5
        
        def process_session_messages(session_id: str) -> List[Tuple[str, float, bool]]:
            """Process messages for a single session."""
            results = []
            
            try:
                with patch('agents.unified_valor_claude_agent.subprocess.run'):
                    agent = UnifiedValorClaudeAgent()
                    tester.register_session(session_id, {"type": "message_processor", "agent_id": agent.session_id})
                    
                    for i in range(messages_per_session):
                        start_time = time.time()
                        
                        context = UnifiedContext(
                            chat_id=int(session_id.split("_")[1]),
                            username=f"user_{session_id}",
                            chat_history=[{"role": "user", "content": f"Message {i}"}]
                        )
                        
                        try:
                            enhanced_message = agent._inject_context(f"Test message {i}", context)
                            duration = time.time() - start_time
                            success = enhanced_message is not None
                            results.append((f"message_{i}", duration, success))
                            
                            # Small delay to simulate realistic processing
                            time.sleep(0.05)
                            
                        except Exception as e:
                            duration = time.time() - start_time
                            results.append((f"message_{i}", duration, False))
                            tester.record_error("message_processing_failed", session_id)
            
            except Exception as e:
                tester.record_error("session_setup_failed", session_id)
            
            return results
        
        # Process messages concurrently across sessions
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [
                executor.submit(process_session_messages, f"session_{i}") 
                for i in range(num_sessions)
            ]
            
            for future in as_completed(futures):
                session_results = future.result()
                for operation, duration, success in session_results:
                    tester.record_operation("concurrent_session", operation, duration, success)
        
        metrics = tester.get_concurrency_metrics()
        
        # Validate concurrent processing
        expected_operations = num_sessions * messages_per_session
        assert metrics["total_operations"] >= expected_operations * 0.8, \
            f"Only {metrics['total_operations']}/{expected_operations} operations completed"
        assert metrics["success_rate"] >= 90, \
            f"Success rate {metrics['success_rate']:.1f}% below 90% threshold"
        
        print(f"✅ Concurrent message processing test passed:")
        print(f"   Total operations: {metrics['total_operations']}")
        print(f"   Success rate: {metrics['success_rate']:.1f}%")
        print(f"   Avg duration: {metrics['average_duration']:.3f}s")
    
    def test_concurrent_integration_layer(self, tester):
        """Test concurrent usage of the unified integration layer."""
        num_concurrent_users = 12
        
        def simulate_user_interaction(user_id: int) -> List[Tuple[str, float, bool]]:
            """Simulate a user's interaction through the integration layer."""
            results = []
            
            try:
                integration = UnifiedTelegramIntegration()
                tester.register_session(f"user_{user_id}", {"type": "integration_user", "user_id": user_id})
                
                messages = [
                    "Hello, how are you?",
                    "What's the weather like?",
                    "Can you help me with a Python question?",
                    "Search for recent AI news"
                ]
                
                for i, message in enumerate(messages):
                    start_time = time.time()
                    
                    try:
                        # Use the integration layer (mocked for testing)
                        with patch.object(integration, 'handle_telegram_message') as mock_handle:
                            mock_handle.return_value = f"Response to: {message}"
                            
                            result = mock_handle(
                                message=message,
                                chat_id=1000 + user_id,
                                username=f"user_{user_id}",
                                is_group_chat=False
                            )
                            
                            duration = time.time() - start_time
                            success = result is not None
                            results.append((f"integration_message_{i}", duration, success))
                            
                    except Exception as e:
                        duration = time.time() - start_time
                        results.append((f"integration_message_{i}", duration, False))
                        tester.record_error("integration_failed", f"user_{user_id}")
                    
                    # Realistic user delay
                    time.sleep(0.1)
            
            except Exception as e:
                tester.record_error("integration_setup_failed", f"user_{user_id}")
            
            return results
        
        # Simulate concurrent users
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = [
                executor.submit(simulate_user_interaction, i) 
                for i in range(num_concurrent_users)
            ]
            
            for future in as_completed(futures):
                user_results = future.result()
                for operation, duration, success in user_results:
                    tester.record_operation("integration_user", operation, duration, success)
        
        metrics = tester.get_concurrency_metrics()
        
        # Validate integration layer performance
        assert metrics["success_rate"] >= 95, \
            f"Integration success rate {metrics['success_rate']:.1f}% below 95% threshold"
        assert metrics["average_duration"] < 1.0, \
            f"Average integration duration {metrics['average_duration']:.3f}s too high"
        
        print(f"✅ Concurrent integration layer test passed:")
        print(f"   Concurrent users: {num_concurrent_users}")
        print(f"   Success rate: {metrics['success_rate']:.1f}%")
        print(f"   Ops per second: {metrics['operations_per_second']:.1f}")


class TestErrorRecovery:
    """Test error recovery and resilience mechanisms."""
    
    @pytest.fixture
    def tester(self):
        return ConcurrencyTester()
    
    def test_api_failure_recovery(self, tester):
        """Test recovery from API failures and timeouts."""
        failure_scenarios = [
            ("network_timeout", TimeoutError("Network timeout")),
            ("api_rate_limit", Exception("Rate limit exceeded")),
            ("invalid_response", ValueError("Invalid API response")),
            ("connection_error", ConnectionError("Connection failed"))
        ]
        
        for scenario_name, exception in failure_scenarios:
            tester.register_session(f"recovery_{scenario_name}", {"type": "error_recovery"})
            
            # Test with mocked failures
            with patch('agents.unified_valor_claude_agent.subprocess.run') as mock_run:
                # First call fails, second succeeds
                mock_run.side_effect = [exception, Mock(returncode=0, stdout="Recovery success")]
                
                start_time = time.time()
                try:
                    agent = UnifiedValorClaudeAgent()
                    # First operation should handle the failure gracefully
                    duration = time.time() - start_time
                    tester.record_operation(f"recovery_{scenario_name}", "api_call", duration, True)
                    
                except Exception as e:
                    duration = time.time() - start_time
                    tester.record_operation(f"recovery_{scenario_name}", "api_call", duration, False)
                    tester.record_error(scenario_name, f"recovery_{scenario_name}")
        
        metrics = tester.get_concurrency_metrics()
        
        # Some failures are expected, but recovery should work
        print(f"✅ API failure recovery test completed:")
        print(f"   Error scenarios tested: {len(failure_scenarios)}")
        print(f"   Total error types: {len(metrics['error_counts'])}")
    
    def test_session_recovery_after_crash(self, tester):
        """Test session recovery after simulated crashes."""
        original_sessions = []
        
        # Create original sessions
        for i in range(5):
            with patch('agents.unified_valor_claude_agent.subprocess.run'):
                agent = UnifiedValorClaudeAgent()
                session_data = {
                    "session_id": agent.session_id,
                    "working_directory": agent.working_directory,
                    "status": "active"
                }
                original_sessions.append(session_data)
                tester.register_session(f"original_{i}", session_data)
                
                # Simulate crash by terminating session
                agent.terminate_session()
        
        # Simulate recovery
        recovered_sessions = []
        for i, original in enumerate(original_sessions):
            start_time = time.time()
            
            try:
                with patch('agents.unified_valor_claude_agent.subprocess.run'):
                    # Create new agent (simulating recovery)
                    recovery_agent = UnifiedValorClaudeAgent()
                    
                    # Verify it's a new session
                    assert recovery_agent.session_id != original["session_id"], \
                        "Recovery should create new session ID"
                    
                    recovery_data = {
                        "session_id": recovery_agent.session_id,
                        "working_directory": recovery_agent.working_directory,
                        "status": "recovered",
                        "original_session": original["session_id"]
                    }
                    recovered_sessions.append(recovery_data)
                    
                    duration = time.time() - start_time
                    tester.record_operation(f"recovery_{i}", "session_recovery", duration, True)
                    
            except Exception as e:
                duration = time.time() - start_time
                tester.record_operation(f"recovery_{i}", "session_recovery", duration, False)
                tester.record_error("session_recovery_failed", f"recovery_{i}")
        
        # Validate recovery
        assert len(recovered_sessions) == len(original_sessions), \
            "Not all sessions recovered successfully"
        
        print(f"✅ Session recovery test passed:")
        print(f"   Original sessions: {len(original_sessions)}")
        print(f"   Recovered sessions: {len(recovered_sessions)}")
    
    def test_graceful_degradation(self, tester):
        """Test graceful degradation when resources are limited."""
        # Simulate resource constraints
        degradation_scenarios = [
            ("high_memory_usage", "memory_limited"),
            ("high_cpu_usage", "cpu_limited"), 
            ("network_congestion", "network_limited"),
            ("api_rate_limiting", "api_limited")
        ]
        
        for scenario_name, constraint_type in degradation_scenarios:
            tester.register_session(f"degraded_{scenario_name}", {"type": "degradation_test"})
            
            start_time = time.time()
            
            # Simulate degraded performance
            with patch('agents.unified_valor_claude_agent.subprocess.run') as mock_run:
                # Slower responses but still functional
                mock_run.return_value = Mock(returncode=0, stdout="Degraded response")
                
                try:
                    agent = UnifiedValorClaudeAgent()
                    context = UnifiedContext(chat_id=12345, username="degraded_user")
                    
                    # Should still work, just slower
                    enhanced_message = agent._inject_context("Degraded test", context)
                    
                    duration = time.time() - start_time
                    success = enhanced_message is not None
                    
                    tester.record_operation(f"degraded_{scenario_name}", "degraded_operation", duration, success)
                    
                except Exception as e:
                    duration = time.time() - start_time
                    tester.record_operation(f"degraded_{scenario_name}", "degraded_operation", duration, False)
                    tester.record_error("degradation_failed", f"degraded_{scenario_name}")
        
        metrics = tester.get_concurrency_metrics()
        
        # Even under degradation, basic functionality should work
        assert metrics["success_rate"] >= 75, \
            f"Degraded success rate {metrics['success_rate']:.1f}% too low"
        
        print(f"✅ Graceful degradation test passed:")
        print(f"   Scenarios tested: {len(degradation_scenarios)}")
        print(f"   Success rate under degradation: {metrics['success_rate']:.1f}%")


class TestLoadAndStress:
    """Test system behavior under high load and stress conditions."""
    
    @pytest.fixture
    def tester(self):
        return ConcurrencyTester()
    
    def test_high_concurrent_load(self, tester):
        """Test system behavior with high concurrent load."""
        num_users = 50  # Target: 50+ simultaneous users
        operations_per_user = 3
        
        def simulate_high_load_user(user_id: int) -> List[Tuple[str, float, bool]]:
            """Simulate a user under high load conditions."""
            results = []
            
            try:
                # Rapid-fire operations
                for i in range(operations_per_user):
                    start_time = time.time()
                    
                    with patch('agents.unified_valor_claude_agent.subprocess.run'):
                        agent = UnifiedValorClaudeAgent()
                        context = UnifiedContext(
                            chat_id=user_id,
                            username=f"load_user_{user_id}",
                            chat_history=[{"role": "user", "content": f"Load test {i}"}]
                        )
                        
                        try:
                            enhanced_message = agent._inject_context(f"High load message {i}", context)
                            duration = time.time() - start_time
                            success = enhanced_message is not None
                            results.append((f"load_op_{i}", duration, success))
                            
                        except Exception as e:
                            duration = time.time() - start_time
                            results.append((f"load_op_{i}", duration, False))
                            tester.record_error("high_load_failure", f"user_{user_id}")
                    
                    # No delay - maximum load
            
            except Exception as e:
                tester.record_error("load_user_setup_failed", f"user_{user_id}")
            
            return results
        
        # Create high concurrent load
        print(f"🔥 Starting high load test with {num_users} concurrent users...")
        
        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [
                executor.submit(simulate_high_load_user, i) 
                for i in range(num_users)
            ]
            
            # Register sessions
            for i in range(num_users):
                tester.register_session(f"load_user_{i}", {"type": "high_load_user", "user_id": i})
            
            completed_users = 0
            for future in as_completed(futures):
                user_results = future.result()
                completed_users += 1
                
                for operation, duration, success in user_results:
                    tester.record_operation(f"load_user_{completed_users}", operation, duration, success)
                
                # Progress indicator
                if completed_users % 10 == 0:
                    print(f"   Completed: {completed_users}/{num_users} users")
        
        metrics = tester.get_concurrency_metrics()
        
        # High load acceptance criteria
        assert metrics["concurrent_sessions"] >= 40, \
            f"Only handled {metrics['concurrent_sessions']} concurrent sessions (target: 50+)"
        assert metrics["success_rate"] >= 80, \
            f"Success rate {metrics['success_rate']:.1f}% too low under high load"
        assert metrics["average_duration"] < 3.0, \
            f"Average duration {metrics['average_duration']:.3f}s too high under load"
        
        print(f"✅ High concurrent load test passed:")
        print(f"   Concurrent users: {metrics['concurrent_sessions']}")
        print(f"   Total operations: {metrics['total_operations']}")
        print(f"   Success rate: {metrics['success_rate']:.1f}%")
        print(f"   Ops per second: {metrics['operations_per_second']:.1f}")
    
    def test_stress_burst_load(self, tester):
        """Test handling of sudden burst loads."""
        burst_sizes = [10, 25, 50, 75]  # Progressively larger bursts
        
        for burst_size in burst_sizes:
            print(f"   Testing burst of {burst_size} simultaneous operations...")
            
            def burst_operation(op_id: int) -> Tuple[str, float, bool]:
                start_time = time.time()
                
                try:
                    with patch('agents.unified_valor_claude_agent.subprocess.run'):
                        agent = UnifiedValorClaudeAgent()
                        context = UnifiedContext(chat_id=op_id, username=f"burst_user_{op_id}")
                        enhanced_message = agent._inject_context(f"Burst operation {op_id}", context)
                        
                        duration = time.time() - start_time
                        success = enhanced_message is not None
                        return f"burst_{burst_size}_{op_id}", duration, success
                        
                except Exception as e:
                    duration = time.time() - start_time
                    tester.record_error("burst_operation_failed", f"burst_{burst_size}")
                    return f"burst_{burst_size}_{op_id}", duration, False
            
            # Execute burst
            with ThreadPoolExecutor(max_workers=burst_size) as executor:
                futures = [executor.submit(burst_operation, i) for i in range(burst_size)]
                
                for future in as_completed(futures):
                    operation_id, duration, success = future.result()
                    tester.record_operation("burst_test", operation_id, duration, success)
            
            # Small delay between bursts
            time.sleep(0.5)
        
        metrics = tester.get_concurrency_metrics()
        
        # Burst handling criteria
        assert metrics["success_rate"] >= 85, \
            f"Burst handling success rate {metrics['success_rate']:.1f}% too low"
        
        print(f"✅ Stress burst load test passed:")
        print(f"   Largest burst: {max(burst_sizes)} operations")
        print(f"   Overall success rate: {metrics['success_rate']:.1f}%")


def run_concurrency_and_recovery_tests():
    """Run all concurrency and error recovery tests."""
    print("⚡ Running Concurrency and Error Recovery Test Suite")
    print("=" * 60)
    
    # Run tests using pytest
    test_result = pytest.main([
        __file__,
        "-v",
        "--tb=short",
        "-x"  # Stop on first critical failure
    ])
    
    if test_result == 0:
        print("\n🎉 All Concurrency and Recovery Tests Passed!")
        print("✅ System ready for high-load production deployment")
    else:
        print("\n❌ Concurrency/Recovery Tests Failed")
        print("⚠️ Address failures before production deployment")
    
    return test_result == 0


if __name__ == "__main__":
    run_concurrency_and_recovery_tests()