#!/usr/bin/env python3
"""
Memory and Resource Monitoring System

Comprehensive resource monitoring for the unified Valor-Claude system,
tracking memory usage, CPU consumption, session management, and system health
to ensure production-ready performance and reliability.

Key Features:
- Real-time memory usage tracking and alerts
- Session lifecycle management and cleanup
- Resource limit enforcement and optimization
- Performance bottleneck detection
- Production health monitoring
"""

import gc
import os
import psutil
import threading
import time
import weakref
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any, Callable
import json


@dataclass
class ResourceSnapshot:
    """Snapshot of system resources at a point in time."""
    timestamp: datetime
    memory_mb: float
    cpu_percent: float
    active_sessions: int
    total_processes: int
    disk_usage_percent: float
    network_io_bytes: Tuple[int, int]  # (bytes_sent, bytes_received)


@dataclass
class SessionInfo:
    """Information about an active session."""
    session_id: str
    chat_id: str
    username: str
    created_at: datetime
    last_activity: datetime
    memory_usage_mb: float
    message_count: int
    status: str = "active"
    context_size_kb: float = 0.0


@dataclass
class ResourceLimits:
    """Configurable resource limits for monitoring."""
    max_memory_mb: float = 500.0
    max_memory_per_session_mb: float = 50.0
    max_cpu_percent: float = 80.0
    max_sessions: int = 100
    session_timeout_hours: float = 24.0
    cleanup_interval_minutes: float = 15.0
    
    # System protection thresholds
    emergency_memory_mb: float = 800.0  # Trigger emergency cleanup
    emergency_cpu_percent: float = 95.0  # Trigger emergency throttling
    critical_memory_mb: float = 1000.0  # Consider process restart
    
    # Auto-restart configuration
    enable_auto_restart: bool = True
    restart_memory_threshold_mb: float = 1200.0
    restart_after_hours: float = 48.0  # Restart after long uptime


@dataclass
class PerformanceAlert:
    """Alert for performance issues."""
    alert_type: str
    severity: str  # low, medium, high, critical
    message: str
    timestamp: datetime
    resource_snapshot: ResourceSnapshot
    recommended_action: str


class ResourceMonitor:
    """
    Comprehensive resource monitoring and management system.
    
    Monitors system resources, tracks active sessions, manages cleanup,
    and provides alerts for production deployment monitoring.
    """
    
    def __init__(self, limits: Optional[ResourceLimits] = None):
        """
        Initialize resource monitor.
        
        Args:
            limits: Resource limits configuration
        """
        self.limits = limits or ResourceLimits()
        
        # Monitoring data
        self.resource_history: deque = deque(maxlen=1000)
        self.active_sessions: Dict[str, SessionInfo] = {}
        self.performance_alerts: List[PerformanceAlert] = []
        
        # Monitoring thread control
        self.monitoring_active = False
        self.monitoring_thread: Optional[threading.Thread] = None
        self.cleanup_thread: Optional[threading.Thread] = None
        
        # Performance tracking
        self.start_time = datetime.now()
        self.total_sessions_created = 0
        self.total_sessions_cleaned = 0
        self.memory_warnings_issued = 0
        
        # Alert callbacks
        self.alert_callbacks: List[Callable] = []
        
        # Session cleanup registry
        self.session_cleanup_callbacks: Dict[str, List[Callable]] = defaultdict(list)
        
        # Resource usage patterns
        self.peak_memory_usage = 0.0
        self.peak_session_count = 0
        self.performance_baselines = self._establish_baselines()
        
        # Emergency protection state
        self.emergency_cleanups_performed = 0
        self.last_emergency_cleanup = None
        self.cpu_throttling_active = False
        self.restart_recommended = False
        self.restart_callbacks: List[Callable] = []
    
    def _establish_baselines(self) -> Dict[str, float]:
        """Establish performance baselines for comparison."""
        try:
            # Take initial measurements
            initial_memory = self._get_memory_usage()
            initial_cpu = psutil.cpu_percent(interval=1)
            
            return {
                "baseline_memory_mb": initial_memory,
                "baseline_cpu_percent": initial_cpu,
                "baseline_timestamp": time.time()
            }
        except Exception:
            return {
                "baseline_memory_mb": 100.0,
                "baseline_cpu_percent": 10.0,
                "baseline_timestamp": time.time()
            }
    
    def start_monitoring(self, monitoring_interval: float = 30.0):
        """
        Start continuous resource monitoring.
        
        Args:
            monitoring_interval: Seconds between monitoring cycles
        """
        if self.monitoring_active:
            return
        
        self.monitoring_active = True
        
        # Start monitoring thread
        self.monitoring_thread = threading.Thread(
            target=self._monitoring_loop,
            args=(monitoring_interval,),
            daemon=True
        )
        self.monitoring_thread.start()
        
        # Start cleanup thread
        cleanup_interval = self.limits.cleanup_interval_minutes * 60
        self.cleanup_thread = threading.Thread(
            target=self._cleanup_loop,
            args=(cleanup_interval,),
            daemon=True
        )
        self.cleanup_thread.start()
        
        print(f"✅ Resource monitoring started (interval: {monitoring_interval}s)")
    
    def stop_monitoring(self):
        """Stop resource monitoring."""
        self.monitoring_active = False
        
        if self.monitoring_thread:
            self.monitoring_thread.join(timeout=5)
        if self.cleanup_thread:
            self.cleanup_thread.join(timeout=5)
        
        print("🛑 Resource monitoring stopped")
    
    def _monitoring_loop(self, interval: float):
        """Main monitoring loop running in background thread."""
        while self.monitoring_active:
            try:
                # Take resource snapshot
                snapshot = self._take_resource_snapshot()
                self.resource_history.append(snapshot)
                
                # Check for alerts
                self._check_resource_alerts(snapshot)
                
                # Update peak usage tracking
                self._update_peak_tracking(snapshot)
                
                # Sleep until next interval
                time.sleep(interval)
                
            except Exception as e:
                print(f"⚠️ Monitoring loop error: {e}")
                time.sleep(interval)
    
    def _cleanup_loop(self, interval: float):
        """Cleanup loop for managing stale sessions and resources."""
        while self.monitoring_active:
            try:
                # Clean up stale sessions
                cleaned_count = self.cleanup_stale_sessions()
                if cleaned_count > 0:
                    print(f"🧹 Cleaned up {cleaned_count} stale sessions")
                
                # Force garbage collection periodically
                gc.collect()
                
                # Sleep until next cleanup
                time.sleep(interval)
                
            except Exception as e:
                print(f"⚠️ Cleanup loop error: {e}")
                time.sleep(interval)
    
    def _take_resource_snapshot(self) -> ResourceSnapshot:
        """Take a snapshot of current system resources."""
        try:
            # Memory usage
            memory_mb = self._get_memory_usage()
            
            # CPU usage
            cpu_percent = psutil.cpu_percent()
            
            # Disk usage
            disk_usage = psutil.disk_usage('/')
            disk_percent = (disk_usage.used / disk_usage.total) * 100
            
            # Network IO
            net_io = psutil.net_io_counters()
            network_io = (net_io.bytes_sent, net_io.bytes_recv)
            
            # Process count
            total_processes = len(psutil.pids())
            
            return ResourceSnapshot(
                timestamp=datetime.now(),
                memory_mb=memory_mb,
                cpu_percent=cpu_percent,
                active_sessions=len(self.active_sessions),
                total_processes=total_processes,
                disk_usage_percent=disk_percent,
                network_io_bytes=network_io
            )
        except Exception as e:
            print(f"⚠️ Error taking resource snapshot: {e}")
            return ResourceSnapshot(
                timestamp=datetime.now(),
                memory_mb=0, cpu_percent=0, active_sessions=0,
                total_processes=0, disk_usage_percent=0,
                network_io_bytes=(0, 0)
            )
    
    def _get_memory_usage(self) -> float:
        """Get current memory usage in MB."""
        try:
            process = psutil.Process()
            return process.memory_info().rss / 1024 / 1024
        except Exception:
            return 0.0
    
    def _check_resource_alerts(self, snapshot: ResourceSnapshot):
        """Check for resource-based alerts and issues."""
        alerts = []
        
        # Emergency protection checks (FIRST PRIORITY)
        if snapshot.memory_mb > self.limits.critical_memory_mb:
            self._handle_critical_memory_situation(snapshot)
            alerts.append(PerformanceAlert(
                alert_type="critical_memory",
                severity="critical",
                message=f"CRITICAL: Memory usage {snapshot.memory_mb:.1f}MB exceeds critical threshold {self.limits.critical_memory_mb}MB",
                timestamp=snapshot.timestamp,
                resource_snapshot=snapshot,
                recommended_action="Emergency cleanup initiated, consider immediate restart"
            ))
        elif snapshot.memory_mb > self.limits.emergency_memory_mb:
            self._handle_emergency_memory_situation(snapshot)
            alerts.append(PerformanceAlert(
                alert_type="emergency_memory",
                severity="high",
                message=f"EMERGENCY: Memory usage {snapshot.memory_mb:.1f}MB requires immediate cleanup",
                timestamp=snapshot.timestamp,
                resource_snapshot=snapshot,
                recommended_action="Emergency session cleanup in progress"
            ))
            
        # Emergency CPU protection
        if snapshot.cpu_percent > self.limits.emergency_cpu_percent:
            self._handle_emergency_cpu_situation(snapshot)
            alerts.append(PerformanceAlert(
                alert_type="emergency_cpu",
                severity="high",
                message=f"EMERGENCY: CPU usage {snapshot.cpu_percent:.1f}% triggering throttling",
                timestamp=snapshot.timestamp,
                resource_snapshot=snapshot,
                recommended_action="CPU throttling enabled, limiting new operations"
            ))
        
        # Check for auto-restart conditions
        if self.limits.enable_auto_restart:
            self._check_auto_restart_conditions(snapshot)
        
        # Standard alerts (existing logic)
        if snapshot.memory_mb > self.limits.max_memory_mb:
            alerts.append(PerformanceAlert(
                alert_type="memory_limit_exceeded",
                severity="high",
                message=f"Memory usage {snapshot.memory_mb:.1f}MB exceeds limit {self.limits.max_memory_mb}MB",
                timestamp=snapshot.timestamp,
                resource_snapshot=snapshot,
                recommended_action="Clean up stale sessions, trigger garbage collection"
            ))
        elif snapshot.memory_mb > self.limits.max_memory_mb * 0.8:
            alerts.append(PerformanceAlert(
                alert_type="memory_warning",
                severity="medium",
                message=f"Memory usage {snapshot.memory_mb:.1f}MB approaching limit",
                timestamp=snapshot.timestamp,
                resource_snapshot=snapshot,
                recommended_action="Monitor closely, prepare for cleanup"
            ))
        
        # CPU alerts
        if snapshot.cpu_percent > self.limits.max_cpu_percent:
            alerts.append(PerformanceAlert(
                alert_type="cpu_limit_exceeded",
                severity="high",
                message=f"CPU usage {snapshot.cpu_percent:.1f}% exceeds limit {self.limits.max_cpu_percent}%",
                timestamp=snapshot.timestamp,
                resource_snapshot=snapshot,
                recommended_action="Investigate high CPU processes, optimize workload"
            ))
        
        # Session count alerts
        if snapshot.active_sessions > self.limits.max_sessions:
            alerts.append(PerformanceAlert(
                alert_type="session_limit_exceeded",
                severity="medium",
                message=f"Active sessions {snapshot.active_sessions} exceeds limit {self.limits.max_sessions}",
                timestamp=snapshot.timestamp,
                resource_snapshot=snapshot,
                recommended_action="Force cleanup of oldest sessions"
            ))
        
        # Process alerts to callbacks
        for alert in alerts:
            self.performance_alerts.append(alert)
            self._trigger_alert_callbacks(alert)
            
            # Update warning counters
            if "memory" in alert.alert_type:
                self.memory_warnings_issued += 1
    
    def _update_peak_tracking(self, snapshot: ResourceSnapshot):
        """Update peak usage tracking."""
        self.peak_memory_usage = max(self.peak_memory_usage, snapshot.memory_mb)
        self.peak_session_count = max(self.peak_session_count, snapshot.active_sessions)
    
    def _trigger_alert_callbacks(self, alert: PerformanceAlert):
        """Trigger registered alert callbacks."""
        for callback in self.alert_callbacks:
            try:
                callback(alert)
            except Exception as e:
                print(f"⚠️ Alert callback error: {e}")
    
    def register_session(self, 
                        session_id: str,
                        chat_id: str, 
                        username: str,
                        cleanup_callback: Optional[Callable] = None) -> SessionInfo:
        """
        Register a new active session.
        
        Args:
            session_id: Unique session identifier
            chat_id: Chat/conversation ID
            username: Username of session owner
            cleanup_callback: Optional cleanup function
            
        Returns:
            SessionInfo object
        """
        session_info = SessionInfo(
            session_id=session_id,
            chat_id=chat_id,
            username=username,
            created_at=datetime.now(),
            last_activity=datetime.now(),
            memory_usage_mb=self._estimate_session_memory(),
            message_count=0
        )
        
        self.active_sessions[session_id] = session_info
        self.total_sessions_created += 1
        
        # Register cleanup callback
        if cleanup_callback:
            self.session_cleanup_callbacks[session_id].append(cleanup_callback)
        
        # Check session limits
        if len(self.active_sessions) > self.limits.max_sessions:
            self._force_session_cleanup()
        
        return session_info
    
    def update_session_activity(self, 
                               session_id: str,
                               memory_delta: float = 0.0,
                               message_count_delta: int = 1,
                               context_size_kb: float = 0.0):
        """
        Update session activity and resource usage.
        
        Args:
            session_id: Session to update
            memory_delta: Change in memory usage (MB)
            message_count_delta: Change in message count
            context_size_kb: Current context size in KB
        """
        if session_id not in self.active_sessions:
            return
        
        session = self.active_sessions[session_id]
        session.last_activity = datetime.now()
        session.memory_usage_mb += memory_delta
        session.message_count += message_count_delta
        session.context_size_kb = context_size_kb
        
        # Check per-session memory limits
        if session.memory_usage_mb > self.limits.max_memory_per_session_mb:
            self._handle_session_memory_limit(session_id, session)
    
    def unregister_session(self, session_id: str) -> bool:
        """
        Unregister and clean up a session.
        
        Args:
            session_id: Session to remove
            
        Returns:
            True if session was found and removed
        """
        if session_id not in self.active_sessions:
            return False
        
        # Run cleanup callbacks
        for callback in self.session_cleanup_callbacks.get(session_id, []):
            try:
                callback()
            except Exception as e:
                print(f"⚠️ Session cleanup callback error: {e}")
        
        # Remove session
        del self.active_sessions[session_id]
        if session_id in self.session_cleanup_callbacks:
            del self.session_cleanup_callbacks[session_id]
        
        self.total_sessions_cleaned += 1
        return True
    
    def cleanup_stale_sessions(self) -> int:
        """
        Clean up sessions that have exceeded timeout.
        
        Returns:
            Number of sessions cleaned up
        """
        current_time = datetime.now()
        timeout_delta = timedelta(hours=self.limits.session_timeout_hours)
        
        stale_sessions = []
        for session_id, session in self.active_sessions.items():
            if current_time - session.last_activity > timeout_delta:
                stale_sessions.append(session_id)
        
        # Clean up stale sessions
        for session_id in stale_sessions:
            self.unregister_session(session_id)
        
        return len(stale_sessions)
    
    def _force_session_cleanup(self):
        """Force cleanup of oldest sessions when limits exceeded."""
        if len(self.active_sessions) <= self.limits.max_sessions:
            return
        
        # Sort sessions by last activity (oldest first)
        sorted_sessions = sorted(
            self.active_sessions.items(),
            key=lambda x: x[1].last_activity
        )
        
        # Remove oldest sessions until under limit
        sessions_to_remove = len(self.active_sessions) - self.limits.max_sessions + 5  # Buffer
        for i in range(min(sessions_to_remove, len(sorted_sessions))):
            session_id = sorted_sessions[i][0]
            self.unregister_session(session_id)
    
    def _handle_session_memory_limit(self, session_id: str, session: SessionInfo):
        """Handle session exceeding memory limits."""
        alert = PerformanceAlert(
            alert_type="session_memory_exceeded",
            severity="medium",
            message=f"Session {session_id} using {session.memory_usage_mb:.1f}MB (limit: {self.limits.max_memory_per_session_mb}MB)",
            timestamp=datetime.now(),
            resource_snapshot=self._take_resource_snapshot(),
            recommended_action=f"Clean up session {session_id} or optimize context usage"
        )
        
        self.performance_alerts.append(alert)
        self._trigger_alert_callbacks(alert)
    
    def _estimate_session_memory(self) -> float:
        """Estimate initial memory usage for a new session."""
        # Base estimate based on current system state
        current_memory = self._get_memory_usage()
        session_count = len(self.active_sessions)
        
        if session_count == 0:
            return 5.0  # Base session memory estimate
        
        # Calculate average memory per existing session
        avg_memory_per_session = current_memory / session_count
        return min(avg_memory_per_session, 10.0)  # Cap at 10MB estimate
    
    def get_system_health(self) -> Dict[str, Any]:
        """Get comprehensive system health report."""
        current_snapshot = self._take_resource_snapshot()
        uptime = datetime.now() - self.start_time
        
        # Calculate averages from recent history
        recent_snapshots = list(self.resource_history)[-10:]  # Last 10 snapshots
        if recent_snapshots:
            avg_memory = sum(s.memory_mb for s in recent_snapshots) / len(recent_snapshots)
            avg_cpu = sum(s.cpu_percent for s in recent_snapshots) / len(recent_snapshots)
        else:
            avg_memory = current_snapshot.memory_mb
            avg_cpu = current_snapshot.cpu_percent
        
        # Health score calculation
        health_score = self._calculate_health_score(current_snapshot)
        
        return {
            "timestamp": current_snapshot.timestamp.isoformat(),
            "uptime_hours": uptime.total_seconds() / 3600,
            "health_score": health_score,
            "current_resources": {
                "memory_mb": current_snapshot.memory_mb,
                "memory_limit_mb": self.limits.max_memory_mb,
                "memory_utilization_percent": (current_snapshot.memory_mb / self.limits.max_memory_mb) * 100,
                "cpu_percent": current_snapshot.cpu_percent,
                "active_sessions": current_snapshot.active_sessions,
                "disk_usage_percent": current_snapshot.disk_usage_percent
            },
            "averages": {
                "memory_mb": avg_memory,
                "cpu_percent": avg_cpu
            },
            "peaks": {
                "memory_mb": self.peak_memory_usage,
                "session_count": self.peak_session_count
            },
            "session_management": {
                "total_created": self.total_sessions_created,
                "total_cleaned": self.total_sessions_cleaned,
                "currently_active": len(self.active_sessions),
                "cleanup_rate": (self.total_sessions_cleaned / max(self.total_sessions_created, 1)) * 100
            },
            "alerts": {
                "total_alerts": len(self.performance_alerts),
                "memory_warnings": self.memory_warnings_issued,
                "recent_alerts": len([a for a in self.performance_alerts 
                                    if (datetime.now() - a.timestamp).total_seconds() < 3600])
            }
        }
    
    def _calculate_health_score(self, snapshot: ResourceSnapshot) -> float:
        """Calculate overall system health score (0-100)."""
        scores = []
        
        # Memory health (30%)
        memory_utilization = snapshot.memory_mb / self.limits.max_memory_mb
        memory_score = max(0, 100 - (memory_utilization * 100))
        scores.append(memory_score * 0.3)
        
        # CPU health (25%)
        cpu_score = max(0, 100 - snapshot.cpu_percent)
        scores.append(cpu_score * 0.25)
        
        # Session health (25%)
        session_utilization = snapshot.active_sessions / self.limits.max_sessions
        session_score = max(0, 100 - (session_utilization * 100))
        scores.append(session_score * 0.25)
        
        # Alert health (20%)
        recent_alerts = len([a for a in self.performance_alerts 
                           if (datetime.now() - a.timestamp).total_seconds() < 3600])
        alert_score = max(0, 100 - (recent_alerts * 10))  # -10 points per recent alert
        scores.append(alert_score * 0.2)
        
        return sum(scores)
    
    def get_session_report(self) -> Dict[str, Any]:
        """Get detailed session management report."""
        sessions_by_age = defaultdict(int)
        sessions_by_size = defaultdict(int)
        total_context_size = 0
        
        for session in self.active_sessions.values():
            # Age categorization
            age_hours = (datetime.now() - session.created_at).total_seconds() / 3600
            if age_hours < 1:
                sessions_by_age["<1h"] += 1
            elif age_hours < 6:
                sessions_by_age["1-6h"] += 1
            elif age_hours < 24:
                sessions_by_age["6-24h"] += 1
            else:
                sessions_by_age[">24h"] += 1
            
            # Memory categorization
            if session.memory_usage_mb < 10:
                sessions_by_size["<10MB"] += 1
            elif session.memory_usage_mb < 25:
                sessions_by_size["10-25MB"] += 1
            elif session.memory_usage_mb < 50:
                sessions_by_size["25-50MB"] += 1
            else:
                sessions_by_size[">50MB"] += 1
            
            total_context_size += session.context_size_kb
        
        return {
            "total_sessions": len(self.active_sessions),
            "sessions_by_age": dict(sessions_by_age),
            "sessions_by_memory": dict(sessions_by_size),
            "total_context_size_mb": total_context_size / 1024,
            "average_session_memory_mb": sum(s.memory_usage_mb for s in self.active_sessions.values()) / max(len(self.active_sessions), 1),
            "most_active_session": max(self.active_sessions.values(), key=lambda s: s.message_count, default=None),
            "oldest_session": min(self.active_sessions.values(), key=lambda s: s.created_at, default=None)
        }
    
    def _handle_emergency_memory_situation(self, snapshot: ResourceSnapshot):
        """Handle emergency memory situation with aggressive cleanup."""
        now = datetime.now()
        
        # Prevent too frequent emergency cleanups (min 60 seconds apart)
        if (self.last_emergency_cleanup and 
            (now - self.last_emergency_cleanup).total_seconds() < 60):
            return
        
        print(f"🚨 EMERGENCY MEMORY CLEANUP: {snapshot.memory_mb:.1f}MB > {self.limits.emergency_memory_mb}MB")
        
        # 1. Force garbage collection
        collected = gc.collect()
        print(f"   - Garbage collection freed {collected} objects")
        
        # 2. Aggressive session cleanup (remove 50% of sessions)
        sessions_to_remove = max(1, len(self.active_sessions) // 2)
        sorted_sessions = sorted(
            self.active_sessions.items(),
            key=lambda x: x[1].last_activity
        )
        
        cleaned_sessions = 0
        for i in range(min(sessions_to_remove, len(sorted_sessions))):
            session_id = sorted_sessions[i][0]
            if self.unregister_session(session_id):
                cleaned_sessions += 1
        
        print(f"   - Emergency cleanup removed {cleaned_sessions} sessions")
        
        # 3. Clear performance history to free memory
        if len(self.resource_history) > 100:
            # Keep only last 50 entries
            recent_history = list(self.resource_history)[-50:]
            self.resource_history.clear()
            self.resource_history.extend(recent_history)
            print(f"   - Trimmed resource history")
        
        self.last_emergency_cleanup = now
        self.emergency_cleanups_performed += 1
    
    def _handle_critical_memory_situation(self, snapshot: ResourceSnapshot):
        """Handle critical memory situation - prepare for restart."""
        print(f"💀 CRITICAL MEMORY SITUATION: {snapshot.memory_mb:.1f}MB > {self.limits.critical_memory_mb}MB")
        
        # 1. Emergency memory cleanup first
        self._handle_emergency_memory_situation(snapshot)
        
        # 2. Mark restart as recommended
        self.restart_recommended = True
        
        # 3. Clear all non-essential data
        self.performance_alerts = self.performance_alerts[-10:]  # Keep only last 10 alerts
        
        # 4. Trigger restart callbacks if configured
        for callback in self.restart_callbacks:
            try:
                callback("critical_memory", snapshot)
            except Exception as e:
                print(f"⚠️ Restart callback error: {e}")
        
        print("   - Restart recommended due to critical memory usage")
    
    def _handle_emergency_cpu_situation(self, snapshot: ResourceSnapshot):
        """Handle emergency CPU situation with throttling."""
        if not self.cpu_throttling_active:
            print(f"🐌 CPU THROTTLING ENABLED: {snapshot.cpu_percent:.1f}% > {self.limits.emergency_cpu_percent}%")
            self.cpu_throttling_active = True
        
        # Add delay to reduce CPU pressure
        time.sleep(0.5)
    
    def _check_auto_restart_conditions(self, snapshot: ResourceSnapshot):
        """Check if automatic restart should be triggered."""
        uptime = datetime.now() - self.start_time
        uptime_hours = uptime.total_seconds() / 3600
        
        should_restart = False
        restart_reason = ""
        
        # Check memory threshold
        if snapshot.memory_mb > self.limits.restart_memory_threshold_mb:
            should_restart = True
            restart_reason = f"memory_threshold ({snapshot.memory_mb:.1f}MB > {self.limits.restart_memory_threshold_mb}MB)"
        
        # Check uptime threshold
        elif uptime_hours > self.limits.restart_after_hours:
            should_restart = True
            restart_reason = f"uptime_threshold ({uptime_hours:.1f}h > {self.limits.restart_after_hours}h)"
        
        if should_restart and not self.restart_recommended:
            self.restart_recommended = True
            print(f"🔄 AUTO-RESTART RECOMMENDED: {restart_reason}")
            
            # Trigger restart callbacks
            for callback in self.restart_callbacks:
                try:
                    callback(restart_reason, snapshot)
                except Exception as e:
                    print(f"⚠️ Restart callback error: {e}")
    
    def add_restart_callback(self, callback: Callable[[str, ResourceSnapshot], None]):
        """Add callback function for restart recommendations."""
        self.restart_callbacks.append(callback)
    
    def add_alert_callback(self, callback: Callable[[PerformanceAlert], None]):
        """Add callback function for performance alerts."""
        self.alert_callbacks.append(callback)
    
    def get_emergency_status(self) -> Dict[str, Any]:
        """Get emergency protection status."""
        return {
            "emergency_cleanups_performed": self.emergency_cleanups_performed,
            "last_emergency_cleanup": self.last_emergency_cleanup.isoformat() if self.last_emergency_cleanup else None,
            "cpu_throttling_active": self.cpu_throttling_active,
            "restart_recommended": self.restart_recommended,
            "protection_thresholds": {
                "emergency_memory_mb": self.limits.emergency_memory_mb,
                "emergency_cpu_percent": self.limits.emergency_cpu_percent,
                "critical_memory_mb": self.limits.critical_memory_mb,
                "restart_memory_threshold_mb": self.limits.restart_memory_threshold_mb
            }
        }
    
    def export_metrics(self, filepath: str):
        """Export comprehensive metrics to JSON file."""
        metrics = {
            "system_health": self.get_system_health(),
            "session_report": self.get_session_report(),
            "resource_history": [
                {
                    "timestamp": snapshot.timestamp.isoformat(),
                    "memory_mb": snapshot.memory_mb,
                    "cpu_percent": snapshot.cpu_percent,
                    "active_sessions": snapshot.active_sessions
                }
                for snapshot in list(self.resource_history)[-100:]  # Last 100 snapshots
            ],
            "recent_alerts": [
                {
                    "type": alert.alert_type,
                    "severity": alert.severity,
                    "message": alert.message,
                    "timestamp": alert.timestamp.isoformat(),
                    "action": alert.recommended_action
                }
                for alert in self.performance_alerts[-50:]  # Last 50 alerts
            ]
        }
        
        with open(filepath, 'w') as f:
            json.dump(metrics, f, indent=2, default=str)
        
        print(f"📊 Metrics exported to {filepath}")
    
    def validate_production_readiness(self) -> Dict[str, bool]:
        """
        Validate system readiness for production deployment.
        
        Returns:
            Dictionary of production readiness checks
        """
        health = self.get_system_health()
        current_snapshot = self._take_resource_snapshot()
        
        return {
            "memory_usage_acceptable": current_snapshot.memory_mb < self.limits.max_memory_mb * 0.8,
            "cpu_usage_acceptable": current_snapshot.cpu_percent < self.limits.max_cpu_percent * 0.8,
            "session_count_manageable": current_snapshot.active_sessions < self.limits.max_sessions * 0.8,
            "health_score_good": health["health_score"] >= 80,
            "no_critical_alerts": len([a for a in self.performance_alerts 
                                     if a.severity == "critical" and 
                                     (datetime.now() - a.timestamp).total_seconds() < 3600]) == 0,
            "cleanup_working": self.total_sessions_cleaned > 0 or self.total_sessions_created < 10,
            "monitoring_active": self.monitoring_active
        }


# Singleton instance for global use
resource_monitor = ResourceMonitor()


# Convenience functions
def start_resource_monitoring():
    """Start global resource monitoring."""
    resource_monitor.start_monitoring()


def get_resource_status() -> Dict[str, Any]:
    """Get current resource status."""
    return resource_monitor.get_system_status()


def register_monitoring_session(session_id: str, chat_id: str, username: str) -> SessionInfo:
    """Register session for resource monitoring."""
    return resource_monitor.register_session(session_id, chat_id, username)