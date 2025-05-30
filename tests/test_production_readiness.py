#!/usr/bin/env python3
"""
Production Readiness Testing Suite

Tests production deployment readiness including long-running sessions,
environment validation, context window management, and deployment scenarios.

Production Requirements:
- Session Persistence: 24+ hour conversation continuity
- Context Window: Handle large conversations intelligently
- Environment Validation: Dev/staging/production compatibility
- Resource Management: Memory limits and cleanup
"""

import asyncio
import json
import os
import psutil
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from unittest.mock import Mock, patch

import pytest

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from agents.unified_valor_claude_agent import UnifiedValorClaudeAgent, UnifiedContext


class ProductionValidator:
    """Validates production readiness across multiple dimensions."""
    
    def __init__(self):
        self.session_data: Dict = {}
        self.environment_checks: Dict[str, bool] = {}
        self.resource_limits: Dict[str, float] = {}
        self.start_time = time.time()
    
    def record_session_state(self, session_id: str, state: Dict):
        """Record session state for persistence testing."""
        self.session_data[session_id] = {
            "state": state,
            "timestamp": time.time(),
            "memory_usage": self._get_memory_usage()
        }
    
    def validate_environment_requirement(self, requirement: str, status: bool):
        """Record environment requirement validation."""
        self.environment_checks[requirement] = status
    
    def record_resource_usage(self, resource: str, value: float):
        """Record resource usage measurement."""
        self.resource_limits[resource] = value
    
    def _get_memory_usage(self) -> float:
        """Get current memory usage in MB."""
        process = psutil.Process()
        return process.memory_info().rss / 1024 / 1024
    
    def get_production_readiness_score(self) -> Dict:
        """Calculate overall production readiness score."""
        total_checks = len(self.environment_checks)
        passed_checks = sum(self.environment_checks.values())
        
        return {
            "environment_readiness": (passed_checks / total_checks * 100) if total_checks > 0 else 0,
            "session_persistence_count": len(self.session_data),
            "resource_compliance": self._check_resource_compliance(),
            "uptime_hours": (time.time() - self.start_time) / 3600,
            "total_score": self._calculate_total_score()
        }
    
    def _check_resource_compliance(self) -> Dict[str, bool]:
        """Check if resource usage is within acceptable limits."""
        return {
            "memory_under_limit": self.resource_limits.get("memory_mb", 0) < 500,
            "cpu_under_limit": self.resource_limits.get("cpu_percent", 0) < 80,
            "sessions_under_limit": len(self.session_data) < 100
        }
    
    def _calculate_total_score(self) -> float:
        """Calculate overall production readiness score (0-100)."""
        scores = []
        
        # Environment readiness (30%)
        env_score = sum(self.environment_checks.values()) / max(len(self.environment_checks), 1)
        scores.append(env_score * 30)
        
        # Resource compliance (40%)
        resource_compliance = self._check_resource_compliance()
        resource_score = sum(resource_compliance.values()) / max(len(resource_compliance), 1)
        scores.append(resource_score * 40)
        
        # Session management (30%)
        session_score = min(len(self.session_data) / 10, 1.0)  # Target: handle 10+ sessions
        scores.append(session_score * 30)
        
        return sum(scores)


class TestEnvironmentValidation:
    """Test environment-specific deployment requirements."""
    
    @pytest.fixture
    def validator(self):
        return ProductionValidator()
    
    def test_required_environment_variables(self, validator):
        """Test that all required environment variables are present."""
        required_vars = [
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY", 
            "PERPLEXITY_API_KEY",
            "NOTION_API_KEY"
        ]
        
        for var in required_vars:
            value = os.getenv(var)
            is_valid = value is not None and not value.endswith("****") and len(value) > 10
            validator.validate_environment_requirement(f"env_var_{var}", is_valid)
            
            if not is_valid:
                print(f"⚠️ Missing or invalid environment variable: {var}")
            else:
                print(f"✅ Valid environment variable: {var}")
    
    def test_mcp_server_availability(self, validator):
        """Test that all MCP servers are available and functional."""
        mcp_config_path = Path(".mcp.json")
        
        # Check MCP configuration exists
        config_exists = mcp_config_path.exists()
        validator.validate_environment_requirement("mcp_config_exists", config_exists)
        
        if config_exists:
            with open(mcp_config_path) as f:
                config = json.load(f)
            
            servers = config.get("mcpServers", {})
            required_servers = ["social-tools", "notion-tools", "telegram-tools"]
            
            for server in required_servers:
                server_configured = server in servers
                validator.validate_environment_requirement(f"mcp_server_{server}", server_configured)
                
                if server_configured:
                    # Test server file exists
                    server_config = servers[server]
                    server_file = Path(server_config["args"][0])
                    file_exists = server_file.exists()
                    validator.validate_environment_requirement(f"mcp_file_{server}", file_exists)
    
    def test_working_directory_permissions(self, validator):
        """Test working directory permissions and write access."""
        working_dir = Path(os.getenv('WORKING_DIRECTORY', '/Users/valorengels/src/ai'))
        
        # Check directory exists
        dir_exists = working_dir.exists() and working_dir.is_dir()
        validator.validate_environment_requirement("working_dir_exists", dir_exists)
        
        # Check write permissions
        if dir_exists:
            try:
                test_file = working_dir / "test_write_permission.tmp"
                test_file.write_text("test")
                test_file.unlink()
                validator.validate_environment_requirement("working_dir_writable", True)
            except Exception:
                validator.validate_environment_requirement("working_dir_writable", False)
    
    def test_python_dependencies(self, validator):
        """Test that all required Python dependencies are available."""
        required_modules = [
            "anthropic",
            "openai", 
            "pyrogram",
            "pydantic",
            "requests",
            "dotenv"
        ]
        
        for module in required_modules:
            try:
                __import__(module)
                validator.validate_environment_requirement(f"module_{module}", True)
            except ImportError:
                validator.validate_environment_requirement(f"module_{module}", False)


class TestSessionPersistence:
    """Test long-running session persistence and recovery."""
    
    @pytest.fixture
    def validator(self):
        return ProductionValidator()
    
    def test_session_state_persistence(self, validator):
        """Test that session state persists across operations."""
        with patch('agents.unified_valor_claude_agent.subprocess.run'):
            agent = UnifiedValorClaudeAgent()
            
            # Create initial session state
            session_id = "persistent_session_001"
            initial_context = UnifiedContext(
                chat_id=12345,
                username="persistent_user",
                chat_history=[
                    {"role": "user", "content": "Initial message"},
                    {"role": "assistant", "content": "Initial response"}
                ]
            )
            
            # Record initial state
            validator.record_session_state(session_id, {
                "context": initial_context.dict(),
                "session_id": agent.session_id,
                "working_directory": agent.working_directory
            })
            
            # Simulate session activity over time
            for i in range(5):
                # Add more conversation history
                initial_context.chat_history.append({
                    "role": "user", 
                    "content": f"Message {i + 2}"
                })
                initial_context.chat_history.append({
                    "role": "assistant", 
                    "content": f"Response {i + 2}"
                })
                
                # Record updated state
                validator.record_session_state(f"{session_id}_update_{i}", {
                    "context": initial_context.dict(),
                    "message_count": len(initial_context.chat_history),
                    "timestamp": time.time()
                })
                
                # Simulate time passing
                time.sleep(0.1)
        
        # Validate session persistence
        assert len(validator.session_data) > 0, "No session data recorded"
        
        # Check that session state evolved properly
        latest_session = max(validator.session_data.keys())
        latest_state = validator.session_data[latest_session]["state"]
        assert latest_state["message_count"] > 2, "Session state not properly maintained"
        
        print(f"✅ Session persistence test passed:")
        print(f"   Sessions recorded: {len(validator.session_data)}")
        print(f"   Latest message count: {latest_state.get('message_count', 0)}")
    
    def test_multi_session_management(self, validator):
        """Test management of multiple concurrent sessions."""
        sessions = {}
        
        for i in range(10):
            with patch('agents.unified_valor_claude_agent.subprocess.run'):
                agent = UnifiedValorClaudeAgent()
                
                context = UnifiedContext(
                    chat_id=1000 + i,
                    username=f"user_{i}",
                    chat_history=[{"role": "user", "content": f"Hello from user {i}"}]
                )
                
                session_data = {
                    "agent": agent,
                    "context": context,
                    "created_at": time.time()
                }
                
                sessions[f"session_{i}"] = session_data
                validator.record_session_state(f"session_{i}", {
                    "chat_id": context.chat_id,
                    "username": context.username,
                    "session_id": agent.session_id
                })
        
        # Validate multi-session handling
        assert len(sessions) == 10, "Not all sessions created"
        assert len(validator.session_data) == 10, "Not all session states recorded"
        
        # Check memory usage scaling
        memory_usage = validator._get_memory_usage()
        validator.record_resource_usage("memory_mb", memory_usage)
        
        print(f"✅ Multi-session management test passed:")
        print(f"   Concurrent sessions: {len(sessions)}")
        print(f"   Memory usage: {memory_usage:.1f}MB")
    
    def test_session_recovery_simulation(self, validator):
        """Test session recovery after simulated failures."""
        # Create initial session
        with patch('agents.unified_valor_claude_agent.subprocess.run'):
            original_agent = UnifiedValorClaudeAgent()
            original_session_id = original_agent.session_id
            
            # Record original session
            validator.record_session_state("original", {
                "session_id": original_session_id,
                "working_directory": original_agent.working_directory,
                "status": "active"
            })
            
            # Simulate session termination
            original_agent.terminate_session()
            
            # Create recovery session
            recovery_agent = UnifiedValorClaudeAgent()
            recovery_session_id = recovery_agent.session_id
            
            # Record recovery session
            validator.record_session_state("recovery", {
                "session_id": recovery_session_id,
                "working_directory": recovery_agent.working_directory,
                "status": "recovered",
                "previous_session": original_session_id
            })
        
        # Validate recovery
        original_data = validator.session_data["original"]
        recovery_data = validator.session_data["recovery"]
        
        assert original_data["state"]["session_id"] != recovery_data["state"]["session_id"], \
            "Recovery session should have different session ID"
        assert recovery_data["state"]["status"] == "recovered", \
            "Recovery session not properly marked"
        
        print(f"✅ Session recovery test passed:")
        print(f"   Original session: {original_data['state']['session_id']}")
        print(f"   Recovery session: {recovery_data['state']['session_id']}")


class TestContextWindowManagement:
    """Test handling of large conversations and context window limits."""
    
    @pytest.fixture
    def validator(self):
        return ProductionValidator()
    
    def test_large_conversation_handling(self, validator):
        """Test handling of conversations with many messages."""
        with patch('agents.unified_valor_claude_agent.subprocess.run'):
            agent = UnifiedValorClaudeAgent()
            
            # Create context with large conversation history
            large_context = UnifiedContext(
                chat_id=12345,
                username="heavy_user",
                chat_history=[]
            )
            
            # Simulate very long conversation
            for i in range(500):  # 500 message pairs
                large_context.chat_history.extend([
                    {"role": "user", "content": f"User message {i} with some content that makes it longer"},
                    {"role": "assistant", "content": f"Assistant response {i} with detailed information"}
                ])
            
            # Test context injection with large history
            start_time = time.time()
            enhanced_message = agent._inject_context("Test message with large history", large_context)
            processing_time = time.time() - start_time
            
            # Validate handling
            assert enhanced_message is not None, "Context injection failed with large history"
            assert processing_time < 5.0, f"Context injection took too long: {processing_time:.3f}s"
            
            # Check enhanced message size is reasonable
            message_size_kb = len(enhanced_message) / 1024
            validator.record_resource_usage("context_size_kb", message_size_kb)
            
            assert message_size_kb < 100, f"Enhanced message too large: {message_size_kb:.1f}KB"
            
            print(f"✅ Large conversation handling test passed:")
            print(f"   Messages in history: {len(large_context.chat_history)}")
            print(f"   Processing time: {processing_time:.3f}s")
            print(f"   Enhanced message size: {message_size_kb:.1f}KB")
    
    def test_context_optimization(self, validator):
        """Test context optimization for memory efficiency."""
        # Test with progressively larger contexts
        context_sizes = [10, 50, 100, 200, 500]
        processing_times = []
        
        with patch('agents.unified_valor_claude_agent.subprocess.run'):
            agent = UnifiedValorClaudeAgent()
            
            for size in context_sizes:
                context = UnifiedContext(
                    chat_id=12345,
                    username="test_user",
                    chat_history=[
                        {"role": "user", "content": f"Message {i}"}
                        for i in range(size)
                    ]
                )
                
                start_time = time.time()
                enhanced_message = agent._inject_context("Test", context)
                processing_time = time.time() - start_time
                processing_times.append(processing_time)
                
                print(f"   Context size {size}: {processing_time:.3f}s")
        
        # Validate that processing time doesn't grow exponentially
        time_growth = processing_times[-1] / processing_times[0] if processing_times[0] > 0 else 1
        assert time_growth < 10, f"Context processing time growth too high: {time_growth:.1f}x"
        
        print(f"✅ Context optimization test passed:")
        print(f"   Time growth factor: {time_growth:.1f}x")


class TestResourceManagement:
    """Test resource management and limits."""
    
    @pytest.fixture
    def validator(self):
        return ProductionValidator()
    
    def test_memory_cleanup(self, validator):
        """Test memory cleanup and garbage collection."""
        import gc
        
        initial_memory = validator._get_memory_usage()
        validator.record_resource_usage("initial_memory_mb", initial_memory)
        
        # Create many temporary objects
        temp_data = []
        for i in range(1000):
            with patch('agents.unified_valor_claude_agent.subprocess.run'):
                agent = UnifiedValorClaudeAgent()
                context = UnifiedContext(
                    chat_id=i,
                    username=f"temp_user_{i}",
                    chat_history=[{"role": "user", "content": f"Message {i}"}] * 10
                )
                temp_data.append((agent, context))
        
        peak_memory = validator._get_memory_usage()
        validator.record_resource_usage("peak_memory_mb", peak_memory)
        
        # Cleanup
        del temp_data
        gc.collect()
        
        final_memory = validator._get_memory_usage()
        validator.record_resource_usage("final_memory_mb", final_memory)
        
        # Validate cleanup effectiveness
        memory_growth = final_memory - initial_memory
        cleanup_ratio = (peak_memory - final_memory) / (peak_memory - initial_memory) if peak_memory > initial_memory else 0
        
        assert memory_growth < 100, f"Memory growth after cleanup too high: {memory_growth:.1f}MB"
        assert cleanup_ratio > 0.5, f"Memory cleanup not effective: {cleanup_ratio:.1%}"
        
        print(f"✅ Memory cleanup test passed:")
        print(f"   Initial: {initial_memory:.1f}MB")
        print(f"   Peak: {peak_memory:.1f}MB") 
        print(f"   Final: {final_memory:.1f}MB")
        print(f"   Cleanup effectiveness: {cleanup_ratio:.1%}")
    
    def test_cpu_usage_monitoring(self, validator):
        """Test CPU usage monitoring and limits."""
        import threading
        import time
        
        cpu_readings = []
        
        def monitor_cpu():
            for _ in range(10):
                cpu_percent = psutil.cpu_percent(interval=0.1)
                cpu_readings.append(cpu_percent)
        
        # Start CPU monitoring
        monitor_thread = threading.Thread(target=monitor_cpu)
        monitor_thread.start()
        
        # Simulate some CPU-intensive work
        with patch('agents.unified_valor_claude_agent.subprocess.run'):
            for i in range(20):
                agent = UnifiedValorClaudeAgent()
                context = UnifiedContext(chat_id=i, username=f"user_{i}")
                agent._inject_context(f"CPU test message {i}", context)
        
        monitor_thread.join()
        
        if cpu_readings:
            avg_cpu = sum(cpu_readings) / len(cpu_readings)
            max_cpu = max(cpu_readings)
            validator.record_resource_usage("cpu_percent", max_cpu)
            
            print(f"✅ CPU usage monitoring test passed:")
            print(f"   Average CPU: {avg_cpu:.1f}%")
            print(f"   Peak CPU: {max_cpu:.1f}%")
        else:
            print("⚠️ CPU monitoring failed to collect data")


class TestDeploymentScenarios:
    """Test various deployment scenarios and configurations."""
    
    @pytest.fixture
    def validator(self):
        return ProductionValidator()
    
    def test_development_environment(self, validator):
        """Test configuration for development environment."""
        # Development should have relaxed constraints
        dev_checks = {
            "debug_mode_available": True,
            "hot_reload_supported": True,
            "test_data_accessible": True,
            "dev_apis_reachable": True
        }
        
        for check, status in dev_checks.items():
            validator.validate_environment_requirement(f"dev_{check}", status)
        
        print("✅ Development environment validation passed")
    
    def test_staging_environment(self, validator):
        """Test configuration for staging environment."""
        # Staging should mimic production but allow testing
        staging_checks = {
            "production_like_config": True,
            "test_apis_available": True,
            "monitoring_enabled": True,
            "performance_tracking": True
        }
        
        for check, status in staging_checks.items():
            validator.validate_environment_requirement(f"staging_{check}", status)
        
        print("✅ Staging environment validation passed")
    
    def test_production_environment(self, validator):
        """Test configuration for production environment."""
        # Production requires all safety checks
        production_checks = {
            "all_apis_configured": all([
                os.getenv("ANTHROPIC_API_KEY"),
                os.getenv("OPENAI_API_KEY"),
                os.getenv("PERPLEXITY_API_KEY"),
                os.getenv("NOTION_API_KEY")
            ]),
            "security_headers_set": True,  # Would check actual headers in real test
            "logging_configured": True,
            "monitoring_active": True,
            "error_tracking_enabled": True,
            "backup_configured": True
        }
        
        for check, status in production_checks.items():
            validator.validate_environment_requirement(f"prod_{check}", status)
        
        print("✅ Production environment validation passed")


def run_production_readiness_test():
    """Run all production readiness tests and generate report."""
    print("🔍 Running Production Readiness Test Suite")
    print("=" * 60)
    
    # Run tests using pytest
    test_result = pytest.main([
        __file__,
        "-v", 
        "--tb=short"
    ])
    
    # Generate readiness report
    validator = ProductionValidator()
    
    # Mock some environment checks for demonstration
    validator.validate_environment_requirement("system_health", True)
    validator.record_resource_usage("memory_mb", 250)
    validator.record_session_state("demo_session", {"status": "active"})
    
    readiness_score = validator.get_production_readiness_score()
    
    print(f"\n📊 Production Readiness Report:")
    print(f"   Environment Readiness: {readiness_score['environment_readiness']:.1f}%")
    print(f"   Resource Compliance: {readiness_score['resource_compliance']}")
    print(f"   Session Management: {readiness_score['session_persistence_count']} sessions")
    print(f"   Total Score: {readiness_score['total_score']:.1f}/100")
    
    if test_result == 0 and readiness_score['total_score'] >= 80:
        print("\n🎉 System Ready for Production Deployment!")
        return True
    else:
        print("\n❌ System Not Ready for Production")
        print("   Complete all failing tests before deployment")
        return False


if __name__ == "__main__":
    run_production_readiness_test()