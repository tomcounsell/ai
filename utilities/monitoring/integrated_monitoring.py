#!/usr/bin/env python3
"""
Integrated Monitoring and Optimization System

Combines all Phase 4 components into a unified monitoring and optimization system
with automatic cleanup triggers, performance optimization, and production-ready
health management for the unified Valor-Claude system.
"""

import asyncio
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass

from .context_window_manager import ContextWindowManager, ContextMetrics
from .streaming_optimizer import StreamingOptimizer, ContentType, StreamingMetrics
from .resource_monitor import ResourceMonitor, ResourceLimits, PerformanceAlert


@dataclass
class SystemOptimizationConfig:
    """Configuration for integrated system optimization."""
    # Automatic cleanup triggers
    memory_cleanup_threshold: float = 0.8  # 80% of limit
    session_cleanup_threshold: float = 0.9  # 90% of limit
    context_optimization_threshold: int = 100  # Messages before optimization
    
    # Performance targets
    target_response_latency: float = 2.0  # seconds
    target_streaming_interval: float = 2.5  # seconds
    target_memory_efficiency: float = 0.7  # 70% compression target
    
    # Monitoring intervals
    health_check_interval: float = 30.0  # seconds
    optimization_interval: float = 60.0  # seconds
    cleanup_interval: float = 300.0  # seconds (5 minutes)


class IntegratedMonitoringSystem:
    """
    Unified monitoring and optimization system for production deployment.
    
    Integrates context management, streaming optimization, and resource monitoring
    with automatic triggers for cleanup, optimization, and health maintenance.
    """
    
    def __init__(self, config: Optional[SystemOptimizationConfig] = None):
        """
        Initialize integrated monitoring system.
        
        Args:
            config: System optimization configuration
        """
        self.config = config or SystemOptimizationConfig()
        
        # Initialize component systems
        self.context_manager = ContextWindowManager(
            max_tokens=100000,
            max_messages=200,
            preserve_recent_count=20
        )
        
        self.streaming_optimizer = StreamingOptimizer(
            target_interval=self.config.target_streaming_interval,
            adaptation_factor=0.3
        )
        
        self.resource_monitor = ResourceMonitor(
            limits=ResourceLimits(
                max_memory_mb=500.0,
                max_sessions=100,
                session_timeout_hours=24.0,
                cleanup_interval_minutes=5.0
            )
        )
        
        # System state
        self.system_active = False
        self.monitoring_threads: List[threading.Thread] = []
        self.optimization_history: List[Dict] = []
        self.alert_handlers: List[Callable] = []
        
        # Performance tracking
        self.performance_metrics = {
            "context_optimizations": 0,
            "automatic_cleanups": 0,
            "streaming_optimizations": 0,
            "health_checks": 0,
            "alerts_triggered": 0,
            "uptime_start": datetime.now()
        }
        
        # Register alert handlers
        self.resource_monitor.add_alert_callback(self._handle_performance_alert)
        
        # Setup logging
        self.logger = logging.getLogger("IntegratedMonitoring")
        self.logger.setLevel(logging.INFO)
    
    def start_monitoring(self):
        """Start integrated monitoring and optimization system."""
        if self.system_active:
            self.logger.warning("Monitoring system already active")
            return
        
        self.system_active = True
        self.performance_metrics["uptime_start"] = datetime.now()
        
        # Start resource monitoring
        self.resource_monitor.start_monitoring(monitoring_interval=30.0)
        
        # Start optimization threads
        threads = [
            ("health_monitor", self._health_monitoring_loop),
            ("optimization_engine", self._optimization_loop),
            ("cleanup_manager", self._cleanup_loop)
        ]
        
        for thread_name, target_func in threads:
            thread = threading.Thread(
                target=target_func,
                name=thread_name,
                daemon=True
            )
            thread.start()
            self.monitoring_threads.append(thread)
        
        self.logger.info("🚀 Integrated monitoring system started")
        print("🚀 Integrated monitoring system started")
    
    def stop_monitoring(self):
        """Stop integrated monitoring system."""
        self.system_active = False
        
        # Stop resource monitoring
        self.resource_monitor.stop_monitoring()
        
        # Wait for threads to complete
        for thread in self.monitoring_threads:
            thread.join(timeout=5)
        
        self.monitoring_threads.clear()
        
        self.logger.info("🛑 Integrated monitoring system stopped")
        print("🛑 Integrated monitoring system stopped")
    
    def _health_monitoring_loop(self):
        """Continuous health monitoring loop."""
        while self.system_active:
            try:
                self._perform_health_check()
                time.sleep(self.config.health_check_interval)
            except Exception as e:
                self.logger.error(f"Health monitoring error: {e}")
                time.sleep(self.config.health_check_interval)
    
    def _optimization_loop(self):
        """Continuous optimization loop."""
        while self.system_active:
            try:
                self._perform_optimization_cycle()
                time.sleep(self.config.optimization_interval)
            except Exception as e:
                self.logger.error(f"Optimization error: {e}")
                time.sleep(self.config.optimization_interval)
    
    def _cleanup_loop(self):
        """Continuous cleanup loop."""
        while self.system_active:
            try:
                self._perform_automatic_cleanup()
                time.sleep(self.config.cleanup_interval)
            except Exception as e:
                self.logger.error(f"Cleanup error: {e}")
                time.sleep(self.config.cleanup_interval)
    
    def _perform_health_check(self):
        """Perform comprehensive system health check."""
        health_data = self.resource_monitor.get_system_health()
        self.performance_metrics["health_checks"] += 1
        
        # Check critical thresholds
        memory_utilization = health_data["current_resources"]["memory_utilization_percent"]
        active_sessions = health_data["current_resources"]["active_sessions"]
        health_score = health_data["health_score"]
        
        # Trigger actions based on health
        if memory_utilization > self.config.memory_cleanup_threshold * 100:
            self._trigger_memory_cleanup()
        
        if active_sessions > self.resource_monitor.limits.max_sessions * self.config.session_cleanup_threshold:
            self._trigger_session_cleanup()
        
        if health_score < 60:
            self._trigger_health_recovery()
        
        # Log health status
        if self.performance_metrics["health_checks"] % 10 == 0:  # Every 10th check
            self.logger.info(f"System health: {health_score:.1f}, Memory: {memory_utilization:.1f}%, Sessions: {active_sessions}")
    
    def _perform_optimization_cycle(self):
        """Perform optimization cycle across all components."""
        optimization_start = time.time()
        optimizations_performed = []
        
        # Optimize streaming performance
        streaming_metrics = self.streaming_optimizer.get_performance_metrics()
        if streaming_metrics.target_compliance_rate < 80:
            recommendations = self.streaming_optimizer.get_optimization_recommendations()
            optimizations_performed.append(f"Streaming optimization: {len(recommendations)} recommendations")
            self.performance_metrics["streaming_optimizations"] += 1
        
        # Check for context optimization opportunities
        # This would be triggered by individual session activity in practice
        context_optimizations = 0
        for session_id in list(self.resource_monitor.active_sessions.keys())[:5]:  # Sample check
            session = self.resource_monitor.active_sessions[session_id]
            if session.message_count > self.config.context_optimization_threshold:
                context_optimizations += 1
        
        if context_optimizations > 0:
            optimizations_performed.append(f"Context optimization candidates: {context_optimizations}")
            self.performance_metrics["context_optimizations"] += context_optimizations
        
        optimization_time = time.time() - optimization_start
        
        # Record optimization cycle
        self.optimization_history.append({
            "timestamp": datetime.now(),
            "duration_ms": optimization_time * 1000,
            "optimizations": optimizations_performed,
            "system_health": self.resource_monitor.get_system_health()["health_score"]
        })
        
        # Maintain history size
        if len(self.optimization_history) > 100:
            self.optimization_history = self.optimization_history[-100:]
    
    def _perform_automatic_cleanup(self):
        """Perform automatic cleanup operations."""
        cleanup_start = time.time()
        cleanup_actions = []
        
        # Clean up stale sessions
        cleaned_sessions = self.resource_monitor.cleanup_stale_sessions()
        if cleaned_sessions > 0:
            cleanup_actions.append(f"Cleaned {cleaned_sessions} stale sessions")
            self.performance_metrics["automatic_cleanups"] += 1
        
        # Force garbage collection for memory management
        import gc
        collected = gc.collect()
        if collected > 0:
            cleanup_actions.append(f"Garbage collected {collected} objects")
        
        cleanup_time = time.time() - cleanup_start
        
        if cleanup_actions:
            self.logger.info(f"Automatic cleanup completed in {cleanup_time*1000:.1f}ms: {'; '.join(cleanup_actions)}")
    
    def _handle_performance_alert(self, alert: PerformanceAlert):
        """Handle performance alerts with automatic responses."""
        self.performance_metrics["alerts_triggered"] += 1
        
        # Log alert
        self.logger.warning(f"Performance alert: {alert.severity} - {alert.message}")
        
        # Automatic responses based on alert type
        if alert.alert_type == "memory_limit_exceeded":
            self._trigger_emergency_cleanup()
        elif alert.alert_type == "session_limit_exceeded":
            self._trigger_session_cleanup()
        elif alert.alert_type == "cpu_limit_exceeded":
            self._trigger_performance_optimization()
        
        # Notify external handlers
        for handler in self.alert_handlers:
            try:
                handler(alert)
            except Exception as e:
                self.logger.error(f"Alert handler error: {e}")
    
    def _trigger_memory_cleanup(self):
        """Trigger memory cleanup procedures."""
        self.logger.info("🧹 Triggering memory cleanup")
        
        # Force garbage collection
        import gc
        gc.collect()
        
        # Clean up oldest sessions
        session_count = len(self.resource_monitor.active_sessions)
        if session_count > self.resource_monitor.limits.max_sessions * 0.8:
            self.resource_monitor._force_session_cleanup()
        
        self.performance_metrics["automatic_cleanups"] += 1
    
    def _trigger_session_cleanup(self):
        """Trigger session cleanup procedures."""
        self.logger.info("📱 Triggering session cleanup")
        
        # Force cleanup of excess sessions
        self.resource_monitor._force_session_cleanup()
        
        # Clean up stale sessions aggressively
        cleaned = self.resource_monitor.cleanup_stale_sessions()
        
        self.performance_metrics["automatic_cleanups"] += 1
    
    def _trigger_emergency_cleanup(self):
        """Trigger emergency cleanup procedures for critical situations."""
        self.logger.warning("🚨 Triggering emergency cleanup")
        
        # Aggressive memory cleanup
        self._trigger_memory_cleanup()
        
        # Aggressive session cleanup
        self._trigger_session_cleanup()
        
        # Force optimization of large contexts
        for session_id, session in list(self.resource_monitor.active_sessions.items()):
            if session.context_size_kb > 50:  # Large contexts
                # In practice, would trigger context optimization for this session
                self.logger.info(f"Large context detected in session {session_id}: {session.context_size_kb}KB")
    
    def _trigger_health_recovery(self):
        """Trigger health recovery procedures."""
        self.logger.warning("💊 Triggering health recovery")
        
        # Comprehensive cleanup
        self._trigger_memory_cleanup()
        
        # Reset performance baselines
        self.streaming_optimizer = StreamingOptimizer(
            target_interval=self.config.target_streaming_interval,
            adaptation_factor=0.3
        )
    
    def _trigger_performance_optimization(self):
        """Trigger performance optimization procedures."""
        self.logger.info("⚡ Triggering performance optimization")
        
        # Optimize streaming performance
        self.performance_metrics["streaming_optimizations"] += 1
        
        # Could trigger other optimizations here
    
    def add_alert_handler(self, handler: Callable[[PerformanceAlert], None]):
        """Add external alert handler."""
        self.alert_handlers.append(handler)
    
    def get_system_status(self) -> Dict[str, Any]:
        """Get comprehensive system status."""
        uptime = datetime.now() - self.performance_metrics["uptime_start"]
        
        return {
            "system_active": self.system_active,
            "uptime_hours": uptime.total_seconds() / 3600,
            "resource_health": self.resource_monitor.get_system_health(),
            "streaming_performance": {
                "total_updates": self.streaming_optimizer.get_performance_metrics().total_updates,
                "average_interval": self.streaming_optimizer.get_performance_metrics().average_interval,
                "target_compliance_rate": self.streaming_optimizer.get_performance_metrics().target_compliance_rate,
                "optimization_score": self.streaming_optimizer.get_performance_metrics().optimization_score
            },
            "performance_metrics": self.performance_metrics,
            "optimization_history_count": len(self.optimization_history),
            "recent_optimizations": self.optimization_history[-5:] if self.optimization_history else [],
            "production_readiness": self._assess_production_readiness()
        }
    
    def _assess_production_readiness(self) -> Dict[str, Any]:
        """Assess overall production readiness."""
        resource_readiness = self.resource_monitor.validate_production_readiness()
        streaming_targets = self.streaming_optimizer.validate_performance_targets()
        
        # Overall readiness score
        resource_score = sum(resource_readiness.values()) / len(resource_readiness)
        streaming_score = sum(streaming_targets.values()) / len(streaming_targets)
        
        overall_ready = resource_score >= 0.8 and streaming_score >= 0.8
        
        return {
            "overall_ready": overall_ready,
            "resource_readiness": resource_readiness,
            "streaming_readiness": streaming_targets,
            "readiness_score": (resource_score + streaming_score) / 2,
            "monitoring_active": self.system_active,
            "automatic_optimization": True,
            "health_score": self.resource_monitor.get_system_health()["health_score"]
        }
    
    def optimize_conversation_context(self, messages: List[Dict], session_id: str) -> tuple:
        """
        Optimize conversation context with integrated monitoring.
        
        Args:
            messages: Conversation messages to optimize
            session_id: Session identifier for tracking
            
        Returns:
            Tuple of (optimized_messages, metrics)
        """
        # Perform context optimization
        optimized, metrics = self.context_manager.optimize_context(messages)
        
        # Update resource monitoring
        if session_id in self.resource_monitor.active_sessions:
            self.resource_monitor.update_session_activity(
                session_id,
                memory_delta=metrics.retained_characters / 1024 / 1024,  # Convert to MB
                context_size_kb=metrics.retained_characters / 1024
            )
        
        # Track optimization
        self.performance_metrics["context_optimizations"] += 1
        
        return optimized, metrics
    
    def optimize_streaming_rate(self, content: str, context: Optional[Dict] = None) -> float:
        """
        Optimize streaming rate with integrated monitoring.
        
        Args:
            content: Content to stream
            context: Additional context for optimization
            
        Returns:
            Optimal streaming interval in seconds
        """
        # Get optimized interval
        interval = self.streaming_optimizer.optimize_streaming_rate(content, context)
        
        # Track optimization
        self.performance_metrics["streaming_optimizations"] += 1
        
        return interval
    
    def register_session(self, session_id: str, chat_id: str, username: str) -> Any:
        """
        Register session with integrated monitoring.
        
        Args:
            session_id: Unique session identifier
            chat_id: Chat/conversation ID
            username: Username of session owner
            
        Returns:
            SessionInfo object
        """
        return self.resource_monitor.register_session(session_id, chat_id, username)
    
    def export_comprehensive_metrics(self, filepath: str):
        """Export comprehensive system metrics."""
        metrics = {
            "system_status": self.get_system_status(),
            "resource_metrics": self.resource_monitor.get_system_health(),
            "streaming_metrics": {
                "total_updates": self.streaming_optimizer.get_performance_metrics().total_updates,
                "average_interval": self.streaming_optimizer.get_performance_metrics().average_interval,
                "target_compliance_rate": self.streaming_optimizer.get_performance_metrics().target_compliance_rate,
                "optimization_score": self.streaming_optimizer.get_performance_metrics().optimization_score
            },
            "session_report": self.resource_monitor.get_session_report(),
            "optimization_history": self.optimization_history[-50:],  # Last 50 optimizations
            "performance_summary": self.performance_metrics
        }
        
        import json
        with open(filepath, 'w') as f:
            json.dump(metrics, f, indent=2, default=str)
        
        self.logger.info(f"📊 Comprehensive metrics exported to {filepath}")
        print(f"📊 Comprehensive metrics exported to {filepath}")


# Global instance for easy access
integrated_monitor: Optional[IntegratedMonitoringSystem] = None

def get_integrated_monitor() -> IntegratedMonitoringSystem:
    """Get or create global integrated monitoring instance."""
    global integrated_monitor
    if integrated_monitor is None:
        integrated_monitor = IntegratedMonitoringSystem()
    return integrated_monitor