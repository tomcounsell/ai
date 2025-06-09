#!/usr/bin/env python3
"""
Automatic Restart Manager

Manages automatic server restarts to prevent system kills due to resource exhaustion.
Integrates with ResourceMonitor to trigger graceful restarts before hitting OS limits.
"""

import os
import signal
import subprocess
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Callable, Dict, Any
import logging

from utilities.monitoring.resource_monitor import ResourceSnapshot, ResourceMonitor

logger = logging.getLogger(__name__)


class AutoRestartManager:
    """
    Manages automatic server restarts to prevent system resource kills.
    
    Features:
    - Monitors resource usage and triggers preventive restarts
    - Graceful shutdown with active session protection  
    - Automatic server startup after restart
    - Configurable restart conditions and policies
    """
    
    def __init__(self, 
                 resource_monitor: ResourceMonitor,
                 restart_script_path: str = "scripts/start.sh",
                 stop_script_path: str = "scripts/stop.sh"):
        """
        Initialize auto-restart manager.
        
        Args:
            resource_monitor: ResourceMonitor instance to monitor
            restart_script_path: Path to startup script
            stop_script_path: Path to stop script
        """
        self.resource_monitor = resource_monitor
        self.restart_script_path = Path(restart_script_path)
        self.stop_script_path = Path(stop_script_path)
        
        # Restart state
        self.restart_scheduled = False
        self.restart_reason = ""
        self.restart_requested_at: Optional[datetime] = None
        self.restart_delay_minutes = 5  # Grace period for active operations
        
        # Statistics
        self.total_restarts_performed = 0
        self.last_restart_time: Optional[datetime] = None
        self.restart_history: list = []
        
        # Configuration
        self.min_restart_interval_hours = 1.0  # Minimum time between restarts
        self.max_restart_delay_minutes = 15  # Maximum delay for graceful shutdown
        
        # Monitoring thread
        self.monitoring_active = False
        self.monitoring_thread: Optional[threading.Thread] = None
        
        # Callbacks
        self.pre_restart_callbacks: list = []
        self.post_restart_callbacks: list = []
        
        # Register with resource monitor
        self.resource_monitor.add_restart_callback(self._handle_restart_request)
        
        logger.info("AutoRestartManager initialized")
    
    def start_monitoring(self, check_interval_seconds: float = 60.0):
        """Start monitoring for restart conditions."""
        if self.monitoring_active:
            return
        
        self.monitoring_active = True
        self.monitoring_thread = threading.Thread(
            target=self._monitoring_loop,
            args=(check_interval_seconds,),
            daemon=True
        )
        self.monitoring_thread.start()
        
        logger.info(f"Auto-restart monitoring started (interval: {check_interval_seconds}s)")
    
    def stop_monitoring(self):
        """Stop restart monitoring."""
        self.monitoring_active = False
        
        if self.monitoring_thread:
            self.monitoring_thread.join(timeout=5)
        
        logger.info("Auto-restart monitoring stopped")
    
    def _monitoring_loop(self, interval: float):
        """Main monitoring loop."""
        while self.monitoring_active:
            try:
                # Check if scheduled restart should be executed
                if self.restart_scheduled:
                    self._check_restart_execution()
                
                time.sleep(interval)
                
            except Exception as e:
                logger.error(f"Auto-restart monitoring loop error: {e}")
                time.sleep(interval)
    
    def _handle_restart_request(self, reason: str, snapshot: ResourceSnapshot):
        """Handle restart request from resource monitor."""
        if self.restart_scheduled:
            logger.info(f"Restart already scheduled, ignoring new request: {reason}")
            return
        
        # Check minimum restart interval
        if self._too_soon_for_restart():
            logger.warning(f"Restart requested too soon after last restart, ignoring: {reason}")
            return
        
        logger.warning(f"🔄 RESTART SCHEDULED: {reason}")
        self.restart_scheduled = True
        self.restart_reason = reason
        self.restart_requested_at = datetime.now()
        
        # Log restart decision
        restart_info = {
            "timestamp": datetime.now().isoformat(),
            "reason": reason,
            "memory_mb": snapshot.memory_mb,
            "cpu_percent": snapshot.cpu_percent,
            "active_sessions": snapshot.active_sessions
        }
        self.restart_history.append(restart_info)
        
        # Keep only last 10 restart records
        if len(self.restart_history) > 10:
            self.restart_history = self.restart_history[-10:]
    
    def _too_soon_for_restart(self) -> bool:
        """Check if it's too soon since last restart."""
        if not self.last_restart_time:
            return False
        
        time_since_last = datetime.now() - self.last_restart_time
        min_interval = timedelta(hours=self.min_restart_interval_hours)
        
        return time_since_last < min_interval
    
    def _check_restart_execution(self):
        """Check if scheduled restart should be executed."""
        if not self.restart_scheduled or not self.restart_requested_at:
            return
        
        # Calculate time since restart was requested
        time_since_request = datetime.now() - self.restart_requested_at
        grace_period = timedelta(minutes=self.restart_delay_minutes)
        max_delay = timedelta(minutes=self.max_restart_delay_minutes)
        
        # Check if we should execute restart
        should_restart = False
        restart_reason = "unknown"
        
        # Execute if grace period passed and no active sessions
        if time_since_request >= grace_period:
            active_sessions = len(self.resource_monitor.active_sessions)
            if active_sessions == 0:
                should_restart = True
                restart_reason = "grace_period_completed_no_active_sessions"
            elif time_since_request >= max_delay:
                should_restart = True
                restart_reason = f"max_delay_exceeded_with_{active_sessions}_sessions"
        
        if should_restart:
            self._execute_restart(restart_reason)
    
    def _execute_restart(self, execution_reason: str):
        """Execute the actual restart process."""
        logger.warning(f"🔄 EXECUTING RESTART: {execution_reason}")
        
        try:
            # 1. Run pre-restart callbacks
            self._run_pre_restart_callbacks()
            
            # 2. Save current state
            self._save_restart_state()
            
            # 3. Stop current server
            logger.info("Stopping current server...")
            if self.stop_script_path.exists():
                subprocess.run([str(self.stop_script_path)], timeout=30)
            else:
                logger.warning(f"Stop script not found: {self.stop_script_path}")
            
            # 4. Brief pause for cleanup
            time.sleep(2)
            
            # 5. Start new server
            logger.info("Starting new server...")
            if self.restart_script_path.exists():
                # Start in background and detach
                subprocess.Popen([str(self.restart_script_path)], 
                               start_new_session=True)
            else:
                logger.error(f"Restart script not found: {self.restart_script_path}")
                return
            
            # 6. Update restart tracking
            self.total_restarts_performed += 1
            self.last_restart_time = datetime.now()
            self.restart_scheduled = False
            self.restart_requested_at = None
            
            # 7. Run post-restart callbacks
            self._run_post_restart_callbacks()
            
            logger.info("✅ Restart process completed successfully")
            
            # 8. Exit current process (new server is now running)
            os._exit(0)
            
        except Exception as e:
            logger.error(f"❌ Restart execution failed: {e}")
            # Reset restart state on failure
            self.restart_scheduled = False
            self.restart_requested_at = None
    
    def _run_pre_restart_callbacks(self):
        """Run pre-restart callbacks."""
        for callback in self.pre_restart_callbacks:
            try:
                callback()
            except Exception as e:
                logger.error(f"Pre-restart callback error: {e}")
    
    def _run_post_restart_callbacks(self):
        """Run post-restart callbacks."""
        for callback in self.post_restart_callbacks:
            try:
                callback()
            except Exception as e:
                logger.error(f"Post-restart callback error: {e}")
    
    def _save_restart_state(self):
        """Save restart state for debugging."""
        state = {
            "restart_time": datetime.now().isoformat(),
            "restart_reason": self.restart_reason,
            "total_restarts": self.total_restarts_performed + 1,
            "resource_status": self.resource_monitor.get_system_health(),
            "emergency_status": self.resource_monitor.get_emergency_status()
        }
        
        try:
            import json
            with open("logs/restart_state.json", "w") as f:
                json.dump(state, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Failed to save restart state: {e}")
    
    def force_restart(self, reason: str = "manual_request"):
        """Force an immediate restart."""
        logger.warning(f"🔄 FORCE RESTART REQUESTED: {reason}")
        
        # Create a fake snapshot for logging
        fake_snapshot = ResourceSnapshot(
            timestamp=datetime.now(),
            memory_mb=0, cpu_percent=0, active_sessions=0,
            total_processes=0, disk_usage_percent=0,
            network_io_bytes=(0, 0)
        )
        
        self._handle_restart_request(reason, fake_snapshot)
        
        # Execute immediately
        self.restart_delay_minutes = 0
        self._execute_restart("force_restart")
    
    def cancel_scheduled_restart(self, reason: str = "manual_cancel"):
        """Cancel a scheduled restart."""
        if self.restart_scheduled:
            logger.info(f"🚫 RESTART CANCELLED: {reason}")
            self.restart_scheduled = False
            self.restart_requested_at = None
            self.restart_reason = ""
        else:
            logger.info("No restart was scheduled to cancel")
    
    def add_pre_restart_callback(self, callback: Callable[[], None]):
        """Add callback to run before restart."""
        self.pre_restart_callbacks.append(callback)
    
    def add_post_restart_callback(self, callback: Callable[[], None]):
        """Add callback to run after restart."""
        self.post_restart_callbacks.append(callback)
    
    def get_restart_status(self) -> Dict[str, Any]:
        """Get current restart status and statistics."""
        return {
            "restart_scheduled": self.restart_scheduled,
            "restart_reason": self.restart_reason,
            "restart_requested_at": self.restart_requested_at.isoformat() if self.restart_requested_at else None,
            "restart_delay_minutes": self.restart_delay_minutes,
            "total_restarts_performed": self.total_restarts_performed,
            "last_restart_time": self.last_restart_time.isoformat() if self.last_restart_time else None,
            "monitoring_active": self.monitoring_active,
            "configuration": {
                "min_restart_interval_hours": self.min_restart_interval_hours,
                "max_restart_delay_minutes": self.max_restart_delay_minutes,
                "restart_script_path": str(self.restart_script_path),
                "stop_script_path": str(self.stop_script_path)
            },
            "restart_history": self.restart_history[-5:]  # Last 5 restarts
        }


# Global instance for easy access
auto_restart_manager: Optional[AutoRestartManager] = None


def initialize_auto_restart(resource_monitor: ResourceMonitor) -> AutoRestartManager:
    """Initialize global auto-restart manager."""
    global auto_restart_manager
    
    auto_restart_manager = AutoRestartManager(resource_monitor)
    auto_restart_manager.start_monitoring()
    
    logger.info("Global auto-restart manager initialized")
    return auto_restart_manager


def get_restart_status() -> Dict[str, Any]:
    """Get restart status from global manager."""
    if auto_restart_manager:
        return auto_restart_manager.get_restart_status()
    else:
        return {"error": "Auto-restart manager not initialized"}


def force_restart(reason: str = "api_request"):
    """Force restart via global manager."""
    if auto_restart_manager:
        auto_restart_manager.force_restart(reason)
    else:
        logger.error("Cannot force restart: auto-restart manager not initialized")