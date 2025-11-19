"""
Auto-restart manager for production process monitoring and recovery.
Handles process monitoring, crash detection, graceful restart, state preservation, and recovery validation.
"""

import os
import sys
import time
import signal
import psutil
import pickle
import json
import logging
import threading
import subprocess
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
import shutil
from enum import Enum

class ProcessState(Enum):
    """Process monitoring states"""
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    CRASHED = "crashed"
    RESTARTING = "restarting"
    MAINTENANCE = "maintenance"

class RestartReason(Enum):
    """Reasons for process restart"""
    CRASH = "crash"
    MEMORY_LEAK = "memory_leak"
    HIGH_CPU = "high_cpu"
    HEALTH_CHECK_FAILED = "health_check_failed"
    MANUAL = "manual"
    CONFIGURATION_CHANGE = "configuration_change"
    SCHEDULED_RESTART = "scheduled_restart"

@dataclass
class ProcessConfig:
    """Configuration for a monitored process"""
    name: str
    command: List[str]
    working_directory: str
    environment: Dict[str, str] = field(default_factory=dict)
    health_check_url: Optional[str] = None
    health_check_interval: int = 30  # seconds
    restart_delay: int = 5  # seconds
    max_restart_attempts: int = 5
    restart_window_minutes: int = 10
    memory_limit_mb: int = 1024  # MB
    cpu_limit_percent: int = 80  # percent
    enable_auto_restart: bool = True
    preserve_state: bool = True
    state_file: Optional[str] = None

@dataclass
class RestartEvent:
    """Record of a restart event"""
    timestamp: datetime
    process_name: str
    reason: RestartReason
    exit_code: Optional[int]
    restart_count: int
    success: bool
    duration_seconds: float
    metadata: Dict[str, Any] = field(default_factory=dict)

class AutoRestartManager:
    """
    Production-grade auto-restart manager with comprehensive process monitoring.
    
    Features:
    - Process health monitoring
    - Automatic crash detection and recovery
    - Resource usage monitoring (CPU, memory)
    - Graceful restart with state preservation
    - Configurable restart policies
    - Health check validation
    - Recovery analytics
    """
    
    def __init__(self, db_path: str = "data/monitoring.db", state_dir: str = "data/process_state"):
        self.db_path = db_path
        self.state_dir = Path(state_dir)
        self.logger = logging.getLogger(__name__)
        
        # Process management
        self.processes: Dict[str, psutil.Popen] = {}
        self.process_configs: Dict[str, ProcessConfig] = {}
        self.process_states: Dict[str, ProcessState] = {}
        self.restart_counts: Dict[str, int] = {}
        self.restart_history: Dict[str, List[datetime]] = {}
        
        # Monitoring state
        self.running = False
        self.monitor_thread = None
        self._shutdown_event = threading.Event()
        
        # Health check tracking
        self._health_check_failures: Dict[str, int] = {}
        self._last_health_checks: Dict[str, datetime] = {}
        
        # State preservation
        self.state_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize database
        self._init_database()
    
    def _init_database(self):
        """Initialize restart manager database tables"""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        with sqlite3.connect(self.db_path) as conn:
            # Process configurations table
            conn.execute('''
                CREATE TABLE IF NOT EXISTS process_configs (
                    name TEXT PRIMARY KEY,
                    command TEXT NOT NULL,
                    working_directory TEXT NOT NULL,
                    environment TEXT,
                    health_check_url TEXT,
                    health_check_interval INTEGER DEFAULT 30,
                    restart_delay INTEGER DEFAULT 5,
                    max_restart_attempts INTEGER DEFAULT 5,
                    restart_window_minutes INTEGER DEFAULT 10,
                    memory_limit_mb INTEGER DEFAULT 1024,
                    cpu_limit_percent INTEGER DEFAULT 80,
                    enable_auto_restart BOOLEAN DEFAULT 1,
                    preserve_state BOOLEAN DEFAULT 1,
                    state_file TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Restart events table
            conn.execute('''
                CREATE TABLE IF NOT EXISTS restart_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME NOT NULL,
                    process_name TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    exit_code INTEGER,
                    restart_count INTEGER NOT NULL,
                    success BOOLEAN NOT NULL,
                    duration_seconds REAL NOT NULL,
                    metadata TEXT
                )
            ''')
            
            # Process status table
            conn.execute('''
                CREATE TABLE IF NOT EXISTS process_status (
                    process_name TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    pid INTEGER,
                    start_time DATETIME,
                    restart_count INTEGER DEFAULT 0,
                    last_health_check DATETIME,
                    memory_usage_mb REAL,
                    cpu_percent REAL,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.execute('CREATE INDEX IF NOT EXISTS idx_restart_timestamp ON restart_events(timestamp)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_restart_process ON restart_events(process_name)')
    
    def add_process(self, config: ProcessConfig):
        """Add a process to be monitored"""
        self.process_configs[config.name] = config
        self.process_states[config.name] = ProcessState.STOPPED
        self.restart_counts[config.name] = 0
        self.restart_history[config.name] = []
        
        # Save configuration to database
        self._save_process_config(config)
        
        self.logger.info(f"Added process for monitoring: {config.name}")
    
    def remove_process(self, process_name: str):
        """Remove a process from monitoring"""
        if process_name in self.processes:
            self.stop_process(process_name)
        
        # Clean up tracking data
        self.process_configs.pop(process_name, None)
        self.process_states.pop(process_name, None)
        self.restart_counts.pop(process_name, None)
        self.restart_history.pop(process_name, None)
        self._health_check_failures.pop(process_name, None)
        self._last_health_checks.pop(process_name, None)
        
        # Remove from database
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('DELETE FROM process_configs WHERE name = ?', (process_name,))
            conn.execute('DELETE FROM process_status WHERE process_name = ?', (process_name,))
        
        self.logger.info(f"Removed process from monitoring: {process_name}")
    
    def start_process(self, process_name: str) -> bool:
        """Start a monitored process"""
        if process_name not in self.process_configs:
            self.logger.error(f"Unknown process: {process_name}")
            return False
        
        if process_name in self.processes:
            if self.processes[process_name].poll() is None:
                self.logger.warning(f"Process {process_name} is already running")
                return True
        
        config = self.process_configs[process_name]
        
        try:
            # Update state
            self.process_states[process_name] = ProcessState.STARTING
            self._update_process_status(process_name)
            
            # Restore state if configured
            if config.preserve_state:
                self._restore_process_state(process_name)
            
            # Start the process
            env = os.environ.copy()
            env.update(config.environment)
            
            process = psutil.Popen(
                config.command,
                cwd=config.working_directory,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            self.processes[process_name] = process
            self.process_states[process_name] = ProcessState.RUNNING
            
            # Update database
            self._update_process_status(process_name, pid=process.pid, start_time=datetime.now())
            
            self.logger.info(f"Started process: {process_name} (PID: {process.pid})")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to start process {process_name}: {e}")
            self.process_states[process_name] = ProcessState.STOPPED
            return False
    
    def stop_process(self, process_name: str, graceful: bool = True) -> bool:
        """Stop a monitored process"""
        if process_name not in self.processes:
            self.logger.warning(f"Process {process_name} is not running")
            return True
        
        process = self.processes[process_name]
        config = self.process_configs[process_name]
        
        try:
            # Update state
            self.process_states[process_name] = ProcessState.STOPPING
            self._update_process_status(process_name)
            
            # Preserve state if configured
            if config.preserve_state:
                self._preserve_process_state(process_name)
            
            # Attempt graceful shutdown first
            if graceful:
                if process.poll() is None:
                    process.terminate()
                    
                    # Wait for graceful shutdown
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        self.logger.warning(f"Process {process_name} did not shut down gracefully, forcing kill")
                        process.kill()
                        process.wait(timeout=5)
            else:
                process.kill()
                process.wait(timeout=5)
            
            # Clean up
            del self.processes[process_name]
            self.process_states[process_name] = ProcessState.STOPPED
            self._update_process_status(process_name, pid=None)
            
            self.logger.info(f"Stopped process: {process_name}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error stopping process {process_name}: {e}")
            return False
    
    def restart_process(self, process_name: str, reason: RestartReason = RestartReason.MANUAL) -> bool:
        """Restart a monitored process"""
        if process_name not in self.process_configs:
            self.logger.error(f"Unknown process: {process_name}")
            return False
        
        config = self.process_configs[process_name]
        start_time = time.time()
        
        try:
            # Update restart tracking
            self.restart_counts[process_name] = self.restart_counts.get(process_name, 0) + 1
            current_time = datetime.now()
            self.restart_history[process_name].append(current_time)
            
            # Check restart limits
            if not self._can_restart(process_name):
                self.logger.error(f"Process {process_name} exceeded restart limits")
                return False
            
            # Update state
            self.process_states[process_name] = ProcessState.RESTARTING
            self._update_process_status(process_name)
            
            # Get exit code if process was running
            exit_code = None
            if process_name in self.processes:
                try:
                    exit_code = self.processes[process_name].poll()
                except:
                    pass
            
            # Stop the process
            self.stop_process(process_name, graceful=True)
            
            # Wait before restarting
            time.sleep(config.restart_delay)
            
            # Start the process
            success = self.start_process(process_name)
            duration = time.time() - start_time
            
            # Record restart event
            event = RestartEvent(
                timestamp=current_time,
                process_name=process_name,
                reason=reason,
                exit_code=exit_code,
                restart_count=self.restart_counts[process_name],
                success=success,
                duration_seconds=duration
            )
            
            self._record_restart_event(event)
            
            if success:
                self.logger.info(f"Successfully restarted process {process_name} (reason: {reason.value})")
            else:
                self.logger.error(f"Failed to restart process {process_name}")
            
            return success
            
        except Exception as e:
            self.logger.error(f"Error restarting process {process_name}: {e}")
            return False
    
    def _can_restart(self, process_name: str) -> bool:
        """Check if process can be restarted based on limits"""
        config = self.process_configs[process_name]
        
        if not config.enable_auto_restart:
            return False
        
        # Check maximum restart attempts
        if self.restart_counts[process_name] >= config.max_restart_attempts:
            return False
        
        # Check restart window
        current_time = datetime.now()
        window_start = current_time - timedelta(minutes=config.restart_window_minutes)
        
        recent_restarts = [
            t for t in self.restart_history[process_name]
            if t >= window_start
        ]
        
        if len(recent_restarts) >= config.max_restart_attempts:
            return False
        
        return True
    
    def start_monitoring(self):
        """Start the monitoring loop"""
        if self.running:
            return
        
        self.running = True
        self._shutdown_event.clear()
        self.monitor_thread = threading.Thread(target=self._monitoring_loop, daemon=True)
        self.monitor_thread.start()
        
        self.logger.info("Auto-restart monitoring started")
    
    def stop_monitoring(self):
        """Stop the monitoring loop"""
        if not self.running:
            return
        
        self.running = False
        self._shutdown_event.set()
        
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=10)
        
        self.logger.info("Auto-restart monitoring stopped")
    
    def _monitoring_loop(self):
        """Main monitoring loop"""
        while not self._shutdown_event.wait(5):  # Check every 5 seconds
            try:
                for process_name in list(self.process_configs.keys()):
                    self._check_process_health(process_name)
            except Exception as e:
                self.logger.error(f"Error in monitoring loop: {e}")
    
    def _check_process_health(self, process_name: str):
        """Check health of a specific process"""
        config = self.process_configs[process_name]
        
        try:
            # Check if process is supposed to be running
            if self.process_states[process_name] in [ProcessState.STOPPED, ProcessState.MAINTENANCE]:
                return
            
            # Check if process exists
            if process_name not in self.processes:
                if self.process_states[process_name] == ProcessState.RUNNING:
                    self.logger.warning(f"Process {process_name} is missing, marking as crashed")
                    self.process_states[process_name] = ProcessState.CRASHED
                    if config.enable_auto_restart:
                        self.restart_process(process_name, RestartReason.CRASH)
                return
            
            process = self.processes[process_name]
            
            # Check if process is still alive
            if process.poll() is not None:
                # Process has exited
                exit_code = process.poll()
                self.logger.warning(f"Process {process_name} exited with code {exit_code}")
                self.process_states[process_name] = ProcessState.CRASHED
                
                if config.enable_auto_restart:
                    self.restart_process(process_name, RestartReason.CRASH)
                return
            
            # Check resource usage
            try:
                process_info = psutil.Process(process.pid)
                memory_mb = process_info.memory_info().rss / 1024 / 1024
                cpu_percent = process_info.cpu_percent()
                
                # Update status
                self._update_process_status(
                    process_name,
                    memory_usage_mb=memory_mb,
                    cpu_percent=cpu_percent
                )
                
                # Check memory limit
                if memory_mb > config.memory_limit_mb:
                    self.logger.warning(f"Process {process_name} exceeds memory limit: {memory_mb:.1f}MB > {config.memory_limit_mb}MB")
                    if config.enable_auto_restart:
                        self.restart_process(process_name, RestartReason.MEMORY_LEAK)
                    return
                
                # Check CPU limit
                if cpu_percent > config.cpu_limit_percent:
                    self.logger.warning(f"Process {process_name} exceeds CPU limit: {cpu_percent:.1f}% > {config.cpu_limit_percent}%")
                    if config.enable_auto_restart:
                        self.restart_process(process_name, RestartReason.HIGH_CPU)
                    return
                
            except psutil.NoSuchProcess:
                self.logger.warning(f"Process {process_name} disappeared during health check")
                self.process_states[process_name] = ProcessState.CRASHED
                if config.enable_auto_restart:
                    self.restart_process(process_name, RestartReason.CRASH)
                return
            
            # Perform health check if configured
            if config.health_check_url:
                self._perform_health_check(process_name)
                
        except Exception as e:
            self.logger.error(f"Error checking health for process {process_name}: {e}")
    
    def _perform_health_check(self, process_name: str):
        """Perform HTTP health check for a process"""
        config = self.process_configs[process_name]
        current_time = datetime.now()
        
        # Check if it's time for a health check
        last_check = self._last_health_checks.get(process_name)
        if last_check:
            if (current_time - last_check).seconds < config.health_check_interval:
                return
        
        try:
            import requests
            
            response = requests.get(config.health_check_url, timeout=10)
            
            if response.status_code == 200:
                # Health check passed
                self._health_check_failures[process_name] = 0
                self._last_health_checks[process_name] = current_time
                self._update_process_status(process_name, last_health_check=current_time)
            else:
                self._handle_health_check_failure(process_name, f"HTTP {response.status_code}")
                
        except Exception as e:
            self._handle_health_check_failure(process_name, str(e))
    
    def _handle_health_check_failure(self, process_name: str, error: str):
        """Handle health check failure"""
        config = self.process_configs[process_name]
        
        failure_count = self._health_check_failures.get(process_name, 0) + 1
        self._health_check_failures[process_name] = failure_count
        
        self.logger.warning(f"Health check failed for {process_name} ({failure_count} times): {error}")
        
        # Restart after 3 consecutive failures
        if failure_count >= 3 and config.enable_auto_restart:
            self.logger.error(f"Process {process_name} failed health checks, restarting")
            self.restart_process(process_name, RestartReason.HEALTH_CHECK_FAILED)
    
    def _preserve_process_state(self, process_name: str):
        """Preserve process state before shutdown"""
        config = self.process_configs[process_name]
        
        if not config.preserve_state:
            return
        
        try:
            state_data = {
                'timestamp': datetime.now().isoformat(),
                'process_name': process_name,
                'restart_count': self.restart_counts.get(process_name, 0),
                'configuration': {
                    'command': config.command,
                    'working_directory': config.working_directory,
                    'environment': config.environment
                }
            }
            
            state_file = self.state_dir / f"{process_name}_state.json"
            with open(state_file, 'w') as f:
                json.dump(state_data, f, indent=2)
            
            self.logger.debug(f"Preserved state for process: {process_name}")
            
        except Exception as e:
            self.logger.error(f"Failed to preserve state for {process_name}: {e}")
    
    def _restore_process_state(self, process_name: str):
        """Restore process state after startup"""
        config = self.process_configs[process_name]
        
        if not config.preserve_state:
            return
        
        try:
            state_file = self.state_dir / f"{process_name}_state.json"
            
            if not state_file.exists():
                return
            
            with open(state_file, 'r') as f:
                state_data = json.load(f)
            
            # Restore restart count
            self.restart_counts[process_name] = state_data.get('restart_count', 0)
            
            self.logger.debug(f"Restored state for process: {process_name}")
            
        except Exception as e:
            self.logger.error(f"Failed to restore state for {process_name}: {e}")
    
    def _save_process_config(self, config: ProcessConfig):
        """Save process configuration to database"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                INSERT OR REPLACE INTO process_configs (
                    name, command, working_directory, environment, health_check_url,
                    health_check_interval, restart_delay, max_restart_attempts,
                    restart_window_minutes, memory_limit_mb, cpu_limit_percent,
                    enable_auto_restart, preserve_state, state_file
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                config.name,
                ' '.join(config.command),
                config.working_directory,
                json.dumps(config.environment),
                config.health_check_url,
                config.health_check_interval,
                config.restart_delay,
                config.max_restart_attempts,
                config.restart_window_minutes,
                config.memory_limit_mb,
                config.cpu_limit_percent,
                config.enable_auto_restart,
                config.preserve_state,
                config.state_file
            ))
    
    def _update_process_status(self, 
                             process_name: str,
                             pid: Optional[int] = None,
                             start_time: Optional[datetime] = None,
                             last_health_check: Optional[datetime] = None,
                             memory_usage_mb: Optional[float] = None,
                             cpu_percent: Optional[float] = None):
        """Update process status in database"""
        with sqlite3.connect(self.db_path) as conn:
            # Build update query dynamically
            updates = ['state = ?', 'updated_at = ?']
            params = [self.process_states[process_name].value, datetime.now()]
            
            if pid is not None:
                updates.append('pid = ?')
                params.append(pid)
            
            if start_time is not None:
                updates.append('start_time = ?')
                params.append(start_time)
            
            if last_health_check is not None:
                updates.append('last_health_check = ?')
                params.append(last_health_check)
            
            if memory_usage_mb is not None:
                updates.append('memory_usage_mb = ?')
                params.append(memory_usage_mb)
            
            if cpu_percent is not None:
                updates.append('cpu_percent = ?')
                params.append(cpu_percent)
            
            updates.append('restart_count = ?')
            params.append(self.restart_counts.get(process_name, 0))
            
            params.append(process_name)
            
            query = f'''
                INSERT OR REPLACE INTO process_status (
                    process_name, {', '.join(updates.replace(' = ?', ''))}
                ) VALUES (?, {', '.join(['?' for _ in updates])})
            '''
            
            # Simplified approach
            conn.execute('''
                INSERT OR REPLACE INTO process_status (
                    process_name, state, pid, start_time, restart_count,
                    last_health_check, memory_usage_mb, cpu_percent, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                process_name,
                self.process_states[process_name].value,
                pid,
                start_time,
                self.restart_counts.get(process_name, 0),
                last_health_check,
                memory_usage_mb,
                cpu_percent,
                datetime.now()
            ))
    
    def _record_restart_event(self, event: RestartEvent):
        """Record restart event in database"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                INSERT INTO restart_events (
                    timestamp, process_name, reason, exit_code, restart_count,
                    success, duration_seconds, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                event.timestamp,
                event.process_name,
                event.reason.value,
                event.exit_code,
                event.restart_count,
                event.success,
                event.duration_seconds,
                json.dumps(event.metadata)
            ))
    
    def get_process_status(self, process_name: Optional[str] = None) -> Dict[str, Any]:
        """Get status of monitored processes"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            if process_name:
                query = 'SELECT * FROM process_status WHERE process_name = ?'
                params = (process_name,)
            else:
                query = 'SELECT * FROM process_status'
                params = ()
            
            results = conn.execute(query, params).fetchall()
            
            if process_name:
                return dict(results[0]) if results else {}
            else:
                return {row['process_name']: dict(row) for row in results}
    
    def get_restart_history(self, process_name: Optional[str] = None, hours: int = 24) -> List[Dict]:
        """Get restart history"""
        start_time = datetime.now() - timedelta(hours=hours)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            if process_name:
                query = '''
                    SELECT * FROM restart_events 
                    WHERE process_name = ? AND timestamp >= ?
                    ORDER BY timestamp DESC
                '''
                params = (process_name, start_time)
            else:
                query = '''
                    SELECT * FROM restart_events 
                    WHERE timestamp >= ?
                    ORDER BY timestamp DESC
                '''
                params = (start_time,)
            
            results = conn.execute(query, params).fetchall()
            return [dict(row) for row in results]
    
    def cleanup(self):
        """Clean up resources and stop all processes"""
        self.logger.info("Auto-restart manager cleanup started")
        
        # Stop monitoring
        self.stop_monitoring()
        
        # Stop all processes
        for process_name in list(self.processes.keys()):
            try:
                self.stop_process(process_name, graceful=True)
            except Exception as e:
                self.logger.error(f"Error stopping process {process_name}: {e}")
        
        self.logger.info("Auto-restart manager cleanup completed")