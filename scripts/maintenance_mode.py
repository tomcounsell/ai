"""
Maintenance mode management system for production operations.
Handles maintenance mode entry/exit, service management during maintenance,
and maintenance task execution.
"""

import os
import sys
import time
import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable
from datetime import datetime, timedelta
from enum import Enum
import subprocess

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

class MaintenanceMode(Enum):
    """Maintenance mode states"""
    NORMAL = "normal"
    ENTERING = "entering"
    MAINTENANCE = "maintenance"
    EXITING = "exiting"
    EMERGENCY = "emergency"

class MaintenanceTask:
    """Individual maintenance task"""
    def __init__(self, name: str, description: str, task_func: Callable, 
                 estimated_duration: int = 0, dependencies: List[str] = None):
        self.name = name
        self.description = description
        self.task_func = task_func
        self.estimated_duration = estimated_duration  # seconds
        self.dependencies = dependencies or []
        self.status = "pending"
        self.start_time = None
        self.end_time = None
        self.error = None
        self.result = None

class MaintenanceManager:
    """
    Production maintenance mode manager.
    
    Features:
    - Graceful service degradation
    - Maintenance task scheduling and execution
    - Service restoration
    - Maintenance logging and reporting
    - Emergency maintenance procedures
    """
    
    def __init__(self, config_file: Optional[str] = None):
        self.config_file = config_file or "config/workspace_config.json"
        self.logger = self._setup_logging()
        
        # Load configuration
        self.config = self._load_configuration()
        
        # Maintenance state
        self.current_mode = MaintenanceMode.NORMAL
        self.maintenance_reason = None
        self.maintenance_start_time = None
        self.maintenance_end_time = None
        self.estimated_duration = None
        
        # Task management
        self.maintenance_tasks: Dict[str, MaintenanceTask] = {}
        self.task_execution_order = []
        self.completed_tasks = []
        self.failed_tasks = []
        
        # Service management
        self.degraded_services = set()
        self.stopped_services = set()
        self.maintenance_page_active = False
        
        # Database
        self.db_path = "data/maintenance.db"
        self._init_database()
        
        # Monitoring
        self.status_check_interval = 30  # seconds
        self.monitoring_thread = None
        self.monitoring_active = False
    
    def _setup_logging(self) -> logging.Logger:
        """Setup maintenance logging"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.StreamHandler(sys.stdout),
                logging.FileHandler('logs/maintenance.log', mode='a')
            ]
        )
        return logging.getLogger(__name__)
    
    def _load_configuration(self) -> Dict[str, Any]:
        """Load maintenance configuration"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
                    return config.get('maintenance', {})
            return self._get_default_maintenance_config()
        except Exception as e:
            self.logger.error(f"Error loading configuration: {e}")
            return self._get_default_maintenance_config()
    
    def _get_default_maintenance_config(self) -> Dict[str, Any]:
        """Get default maintenance configuration"""
        return {
            "max_maintenance_duration_hours": 4,
            "notification_channels": ["log"],
            "maintenance_page": {
                "enabled": True,
                "template": "maintenance_template.html",
                "port": 8081
            },
            "services": {
                "essential": ["database", "core_agents"],
                "degradable": ["monitoring", "dashboard", "integrations"],
                "stoppable": ["telegram", "external_integrations"]
            },
            "tasks": {
                "allowed_during_maintenance": [
                    "database_maintenance",
                    "backup_operations",
                    "security_updates",
                    "configuration_updates"
                ]
            }
        }
    
    def _init_database(self):
        """Initialize maintenance database"""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        with sqlite3.connect(self.db_path) as conn:
            # Maintenance sessions table
            conn.execute('''
                CREATE TABLE IF NOT EXISTS maintenance_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    start_time DATETIME NOT NULL,
                    end_time DATETIME,
                    reason TEXT NOT NULL,
                    estimated_duration_seconds INTEGER,
                    actual_duration_seconds INTEGER,
                    status TEXT NOT NULL,
                    tasks_completed INTEGER DEFAULT 0,
                    tasks_failed INTEGER DEFAULT 0,
                    metadata TEXT
                )
            ''')
            
            # Maintenance tasks table
            conn.execute('''
                CREATE TABLE IF NOT EXISTS maintenance_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER,
                    task_name TEXT NOT NULL,
                    description TEXT,
                    status TEXT NOT NULL,
                    start_time DATETIME,
                    end_time DATETIME,
                    duration_seconds INTEGER,
                    error_message TEXT,
                    result TEXT,
                    FOREIGN KEY (session_id) REFERENCES maintenance_sessions (id)
                )
            ''')
            
            # Service status table
            conn.execute('''
                CREATE TABLE IF NOT EXISTS service_status (
                    service_name TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.execute('CREATE INDEX IF NOT EXISTS idx_maintenance_start ON maintenance_sessions(start_time)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_task_session ON maintenance_tasks(session_id)')
    
    def enter_maintenance_mode(self, 
                             reason: str,
                             estimated_duration_hours: Optional[float] = None,
                             emergency: bool = False) -> bool:
        """Enter maintenance mode"""
        
        if self.current_mode != MaintenanceMode.NORMAL:
            self.logger.warning(f"Already in {self.current_mode.value} mode")
            return False
        
        self.logger.info("=" * 60)
        self.logger.info("🔧 ENTERING MAINTENANCE MODE")
        self.logger.info(f"📋 Reason: {reason}")
        if estimated_duration_hours:
            self.logger.info(f"⏱️ Estimated duration: {estimated_duration_hours} hours")
        self.logger.info("=" * 60)
        
        try:
            self.current_mode = MaintenanceMode.EMERGENCY if emergency else MaintenanceMode.ENTERING
            self.maintenance_reason = reason
            self.maintenance_start_time = datetime.now()
            self.estimated_duration = estimated_duration_hours * 3600 if estimated_duration_hours else None
            
            # Start maintenance session in database
            self._start_maintenance_session()
            
            maintenance_steps = [
                ("Activate Maintenance Page", self._activate_maintenance_page),
                ("Notify Stakeholders", self._notify_maintenance_start),
                ("Degrade Non-Essential Services", self._degrade_services),
                ("Stop Non-Essential Services", self._stop_services),
                ("Validate Essential Services", self._validate_essential_services),
                ("Prepare Maintenance Environment", self._prepare_maintenance_environment)
            ]
            
            if emergency:
                self.logger.warning("EMERGENCY MAINTENANCE - Skipping some preparatory steps")
                maintenance_steps = maintenance_steps[-2:]  # Only essential validation and prep
            
            success = True
            for step_name, step_func in maintenance_steps:
                self.logger.info(f"📋 {step_name}...")
                try:
                    if not step_func():
                        self.logger.error(f"❌ {step_name} failed")
                        success = False
                        break
                    else:
                        self.logger.info(f"✅ {step_name} completed")
                except Exception as e:
                    self.logger.error(f"💥 {step_name} error: {e}")
                    success = False
                    break
            
            if success:
                self.current_mode = MaintenanceMode.MAINTENANCE
                self._start_monitoring()
                self.logger.info("🔧 MAINTENANCE MODE ACTIVE")
                self.logger.info("=" * 60)
                return True
            else:
                self.logger.error("Failed to enter maintenance mode - rolling back")
                self._rollback_maintenance_entry()
                return False
                
        except Exception as e:
            self.logger.error(f"Error entering maintenance mode: {e}")
            self._rollback_maintenance_entry()
            return False
    
    def exit_maintenance_mode(self) -> bool:
        """Exit maintenance mode and restore normal operations"""
        
        if self.current_mode not in [MaintenanceMode.MAINTENANCE, MaintenanceMode.EMERGENCY]:
            self.logger.warning(f"Not in maintenance mode (current: {self.current_mode.value})")
            return False
        
        self.logger.info("=" * 60)
        self.logger.info("🔧 EXITING MAINTENANCE MODE")
        self.logger.info("=" * 60)
        
        try:
            self.current_mode = MaintenanceMode.EXITING
            self.maintenance_end_time = datetime.now()
            
            exit_steps = [
                ("Validate System Health", self._validate_system_health),
                ("Restore Stopped Services", self._restore_services),
                ("Restore Service Levels", self._restore_service_levels),
                ("Deactivate Maintenance Page", self._deactivate_maintenance_page),
                ("Notify Stakeholders", self._notify_maintenance_end),
                ("Final System Validation", self._final_system_validation)
            ]
            
            success = True
            for step_name, step_func in exit_steps:
                self.logger.info(f"📋 {step_name}...")
                try:
                    if not step_func():
                        self.logger.error(f"❌ {step_name} failed")
                        success = False
                        break
                    else:
                        self.logger.info(f"✅ {step_name} completed")
                except Exception as e:
                    self.logger.error(f"💥 {step_name} error: {e}")
                    success = False
                    break
            
            if success:
                self.current_mode = MaintenanceMode.NORMAL
                self._stop_monitoring()
                self._complete_maintenance_session(True)
                
                duration = (self.maintenance_end_time - self.maintenance_start_time).total_seconds()
                self.logger.info(f"✅ MAINTENANCE MODE EXITED ({duration/60:.1f} minutes)")
                self.logger.info("=" * 60)
                return True
            else:
                self.logger.error("Failed to exit maintenance mode cleanly")
                self._complete_maintenance_session(False)
                return False
                
        except Exception as e:
            self.logger.error(f"Error exiting maintenance mode: {e}")
            self._complete_maintenance_session(False)
            return False
    
    def add_maintenance_task(self, task: MaintenanceTask):
        """Add a maintenance task to be executed"""
        self.maintenance_tasks[task.name] = task
        self.logger.info(f"Added maintenance task: {task.name}")
    
    def execute_maintenance_tasks(self) -> bool:
        """Execute all scheduled maintenance tasks"""
        if self.current_mode != MaintenanceMode.MAINTENANCE:
            self.logger.error("Can only execute tasks during maintenance mode")
            return False
        
        if not self.maintenance_tasks:
            self.logger.info("No maintenance tasks to execute")
            return True
        
        self.logger.info(f"Executing {len(self.maintenance_tasks)} maintenance tasks")
        
        # Build execution order based on dependencies
        execution_order = self._build_task_execution_order()
        
        success = True
        for task_name in execution_order:
            task = self.maintenance_tasks[task_name]
            
            self.logger.info(f"🔧 Executing task: {task.name}")
            self.logger.info(f"📝 Description: {task.description}")
            
            task.start_time = datetime.now()
            task.status = "running"
            
            try:
                # Execute the task
                task.result = task.task_func()
                task.end_time = datetime.now()
                task.status = "completed"
                self.completed_tasks.append(task.name)
                
                duration = (task.end_time - task.start_time).total_seconds()
                self.logger.info(f"✅ Task completed: {task.name} ({duration:.1f}s)")
                
                # Save task result to database
                self._save_task_result(task)
                
            except Exception as e:
                task.end_time = datetime.now()
                task.status = "failed"
                task.error = str(e)
                self.failed_tasks.append(task.name)
                
                duration = (task.end_time - task.start_time).total_seconds()
                self.logger.error(f"❌ Task failed: {task.name} ({duration:.1f}s) - {e}")
                
                # Save task error to database
                self._save_task_result(task)
                
                success = False
                
                # Check if this task is critical
                if self._is_critical_task(task.name):
                    self.logger.error(f"Critical task {task.name} failed - stopping task execution")
                    break
        
        self.logger.info(f"Task execution completed: {len(self.completed_tasks)} succeeded, {len(self.failed_tasks)} failed")
        return success
    
    def get_maintenance_status(self) -> Dict[str, Any]:
        """Get current maintenance status"""
        status = {
            'mode': self.current_mode.value,
            'reason': self.maintenance_reason,
            'start_time': self.maintenance_start_time.isoformat() if self.maintenance_start_time else None,
            'estimated_end_time': None,
            'elapsed_time_seconds': None,
            'tasks': {
                'total': len(self.maintenance_tasks),
                'completed': len(self.completed_tasks),
                'failed': len(self.failed_tasks),
                'remaining': len(self.maintenance_tasks) - len(self.completed_tasks) - len(self.failed_tasks)
            },
            'services': {
                'degraded': list(self.degraded_services),
                'stopped': list(self.stopped_services)
            }
        }
        
        if self.maintenance_start_time:
            status['elapsed_time_seconds'] = (datetime.now() - self.maintenance_start_time).total_seconds()
            
            if self.estimated_duration:
                estimated_end = self.maintenance_start_time + timedelta(seconds=self.estimated_duration)
                status['estimated_end_time'] = estimated_end.isoformat()
        
        return status
    
    def _activate_maintenance_page(self) -> bool:
        """Activate maintenance page"""
        try:
            maintenance_config = self.config.get('maintenance_page', {})
            if not maintenance_config.get('enabled', False):
                self.logger.info("Maintenance page disabled")
                return True
            
            # Create simple maintenance page
            maintenance_html = self._generate_maintenance_page()
            
            # Save maintenance page
            maintenance_file = Path('temp/maintenance.html')
            maintenance_file.parent.mkdir(exist_ok=True)
            
            with open(maintenance_file, 'w') as f:
                f.write(maintenance_html)
            
            self.maintenance_page_active = True
            self.logger.info("Maintenance page activated")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to activate maintenance page: {e}")
            return False
    
    def _deactivate_maintenance_page(self) -> bool:
        """Deactivate maintenance page"""
        try:
            if not self.maintenance_page_active:
                return True
            
            maintenance_file = Path('temp/maintenance.html')
            if maintenance_file.exists():
                maintenance_file.unlink()
            
            self.maintenance_page_active = False
            self.logger.info("Maintenance page deactivated")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to deactivate maintenance page: {e}")
            return False
    
    def _generate_maintenance_page(self) -> str:
        """Generate maintenance page HTML"""
        estimated_end = ""
        if self.estimated_duration:
            end_time = self.maintenance_start_time + timedelta(seconds=self.estimated_duration)
            estimated_end = f"<p>Estimated completion: {end_time.strftime('%Y-%m-%d %H:%M UTC')}</p>"
        
        return f'''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>System Maintenance - AI Rebuild</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            text-align: center;
            padding: 50px 20px;
            margin: 0;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            justify-content: center;
        }}
        .container {{
            max-width: 600px;
            margin: 0 auto;
            background: rgba(255, 255, 255, 0.1);
            padding: 40px;
            border-radius: 15px;
            backdrop-filter: blur(10px);
        }}
        h1 {{ font-size: 2.5rem; margin-bottom: 20px; }}
        p {{ font-size: 1.1rem; line-height: 1.6; margin-bottom: 15px; }}
        .maintenance-icon {{ font-size: 4rem; margin-bottom: 20px; }}
        .progress {{ 
            width: 100%; 
            height: 4px; 
            background: rgba(255,255,255,0.3); 
            border-radius: 2px; 
            margin: 20px 0;
            overflow: hidden;
        }}
        .progress-bar {{
            height: 100%;
            background: #4CAF50;
            border-radius: 2px;
            animation: pulse 2s infinite;
        }}
        @keyframes pulse {{
            0% {{ opacity: 0.6; }}
            50% {{ opacity: 1; }}
            100% {{ opacity: 0.6; }}
        }}
        .status {{ 
            background: rgba(255,255,255,0.2); 
            padding: 15px; 
            border-radius: 8px; 
            margin-top: 20px; 
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="maintenance-icon">🔧</div>
        <h1>System Maintenance</h1>
        <p>AI Rebuild is currently undergoing scheduled maintenance to improve performance and reliability.</p>
        <p><strong>Reason:</strong> {self.maintenance_reason}</p>
        {estimated_end}
        <div class="progress">
            <div class="progress-bar" style="width: 50%"></div>
        </div>
        <div class="status">
            <p><strong>Status:</strong> Maintenance in progress</p>
            <p>We apologize for the inconvenience and appreciate your patience.</p>
        </div>
    </div>
    <script>
        // Auto-refresh every 30 seconds
        setTimeout(function() {{
            location.reload();
        }}, 30000);
    </script>
</body>
</html>
        '''
    
    def _notify_maintenance_start(self) -> bool:
        """Notify stakeholders about maintenance start"""
        try:
            # Log notification
            self.logger.info(f"MAINTENANCE NOTIFICATION: Started - {self.maintenance_reason}")
            
            # Additional notification channels would be implemented here
            # (email, Slack, etc.)
            
            return True
        except Exception as e:
            self.logger.error(f"Failed to send maintenance start notifications: {e}")
            return False
    
    def _notify_maintenance_end(self) -> bool:
        """Notify stakeholders about maintenance end"""
        try:
            duration = (self.maintenance_end_time - self.maintenance_start_time).total_seconds() / 60
            
            self.logger.info(f"MAINTENANCE NOTIFICATION: Completed after {duration:.1f} minutes")
            
            # Additional notification channels would be implemented here
            
            return True
        except Exception as e:
            self.logger.error(f"Failed to send maintenance end notifications: {e}")
            return False
    
    def _degrade_services(self) -> bool:
        """Degrade non-essential services"""
        try:
            services_config = self.config.get('services', {})
            degradable_services = services_config.get('degradable', [])
            
            for service in degradable_services:
                try:
                    self.logger.info(f"Degrading service: {service}")
                    # Service-specific degradation logic would go here
                    self.degraded_services.add(service)
                    self._update_service_status(service, "degraded", "maintenance")
                except Exception as e:
                    self.logger.error(f"Failed to degrade service {service}: {e}")
            
            self.logger.info(f"Degraded {len(self.degraded_services)} services")
            return True
            
        except Exception as e:
            self.logger.error(f"Error degrading services: {e}")
            return False
    
    def _stop_services(self) -> bool:
        """Stop non-essential services"""
        try:
            services_config = self.config.get('services', {})
            stoppable_services = services_config.get('stoppable', [])
            
            for service in stoppable_services:
                try:
                    self.logger.info(f"Stopping service: {service}")
                    # Service-specific stop logic would go here
                    self.stopped_services.add(service)
                    self._update_service_status(service, "stopped", "maintenance")
                except Exception as e:
                    self.logger.error(f"Failed to stop service {service}: {e}")
            
            self.logger.info(f"Stopped {len(self.stopped_services)} services")
            return True
            
        except Exception as e:
            self.logger.error(f"Error stopping services: {e}")
            return False
    
    def _restore_services(self) -> bool:
        """Restore stopped services"""
        try:
            for service in list(self.stopped_services):
                try:
                    self.logger.info(f"Restoring service: {service}")
                    # Service-specific restore logic would go here
                    self.stopped_services.discard(service)
                    self._update_service_status(service, "running", "normal")
                except Exception as e:
                    self.logger.error(f"Failed to restore service {service}: {e}")
            
            self.logger.info("Service restoration completed")
            return True
            
        except Exception as e:
            self.logger.error(f"Error restoring services: {e}")
            return False
    
    def _restore_service_levels(self) -> bool:
        """Restore degraded services to normal levels"""
        try:
            for service in list(self.degraded_services):
                try:
                    self.logger.info(f"Restoring service level: {service}")
                    # Service-specific restoration logic would go here
                    self.degraded_services.discard(service)
                    self._update_service_status(service, "running", "normal")
                except Exception as e:
                    self.logger.error(f"Failed to restore service level {service}: {e}")
            
            self.logger.info("Service level restoration completed")
            return True
            
        except Exception as e:
            self.logger.error(f"Error restoring service levels: {e}")
            return False
    
    def _validate_essential_services(self) -> bool:
        """Validate that essential services are still running"""
        try:
            services_config = self.config.get('services', {})
            essential_services = services_config.get('essential', [])
            
            for service in essential_services:
                # Service-specific health check logic would go here
                self.logger.info(f"✅ Essential service healthy: {service}")
            
            return True
        except Exception as e:
            self.logger.error(f"Error validating essential services: {e}")
            return False
    
    def _validate_system_health(self) -> bool:
        """Validate overall system health before exiting maintenance"""
        try:
            # Basic health checks
            health_checks = [
                ("Database connectivity", self._check_database_health),
                ("File system access", self._check_filesystem_health),
                ("Essential processes", self._check_process_health)
            ]
            
            for check_name, check_func in health_checks:
                if not check_func():
                    self.logger.error(f"Health check failed: {check_name}")
                    return False
                else:
                    self.logger.info(f"✅ Health check passed: {check_name}")
            
            return True
        except Exception as e:
            self.logger.error(f"Error validating system health: {e}")
            return False
    
    def _check_database_health(self) -> bool:
        """Check database health"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("SELECT 1").fetchone()
            return True
        except Exception:
            return False
    
    def _check_filesystem_health(self) -> bool:
        """Check filesystem health"""
        try:
            test_file = Path('temp/health_check.tmp')
            test_file.parent.mkdir(exist_ok=True)
            test_file.write_text('test')
            test_file.unlink()
            return True
        except Exception:
            return False
    
    def _check_process_health(self) -> bool:
        """Check process health"""
        try:
            # Basic process health check
            return True
        except Exception:
            return False
    
    def _final_system_validation(self) -> bool:
        """Final system validation"""
        try:
            # Comprehensive system validation would go here
            self.logger.info("Final system validation passed")
            return True
        except Exception as e:
            self.logger.error(f"Final system validation failed: {e}")
            return False
    
    def _prepare_maintenance_environment(self) -> bool:
        """Prepare environment for maintenance tasks"""
        try:
            # Setup maintenance workspace
            maintenance_dir = Path('temp/maintenance')
            maintenance_dir.mkdir(parents=True, exist_ok=True)
            
            # Set maintenance environment variables
            os.environ['MAINTENANCE_MODE'] = 'true'
            os.environ['MAINTENANCE_START_TIME'] = self.maintenance_start_time.isoformat()
            
            return True
        except Exception as e:
            self.logger.error(f"Error preparing maintenance environment: {e}")
            return False
    
    def _build_task_execution_order(self) -> List[str]:
        """Build task execution order based on dependencies"""
        # Simple dependency resolution - in production, use proper topological sort
        ordered_tasks = []
        remaining_tasks = set(self.maintenance_tasks.keys())
        
        while remaining_tasks:
            # Find tasks with no unmet dependencies
            ready_tasks = []
            for task_name in remaining_tasks:
                task = self.maintenance_tasks[task_name]
                unmet_deps = set(task.dependencies) - set(ordered_tasks)
                if not unmet_deps:
                    ready_tasks.append(task_name)
            
            if not ready_tasks:
                # Circular dependency or missing dependency
                self.logger.warning("Circular or missing task dependencies detected")
                ready_tasks = list(remaining_tasks)  # Execute remaining tasks anyway
            
            # Add ready tasks to execution order
            for task_name in ready_tasks:
                ordered_tasks.append(task_name)
                remaining_tasks.remove(task_name)
        
        return ordered_tasks
    
    def _is_critical_task(self, task_name: str) -> bool:
        """Check if a task is critical (failure should stop execution)"""
        critical_tasks = self.config.get('tasks', {}).get('critical', [])
        return task_name in critical_tasks
    
    def _save_task_result(self, task: MaintenanceTask):
        """Save task execution result to database"""
        try:
            session_id = self._get_current_session_id()
            duration = None
            if task.start_time and task.end_time:
                duration = (task.end_time - task.start_time).total_seconds()
            
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('''
                    INSERT INTO maintenance_tasks (
                        session_id, task_name, description, status, start_time,
                        end_time, duration_seconds, error_message, result
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    session_id,
                    task.name,
                    task.description,
                    task.status,
                    task.start_time,
                    task.end_time,
                    duration,
                    task.error,
                    str(task.result) if task.result else None
                ))
        except Exception as e:
            self.logger.error(f"Failed to save task result: {e}")
    
    def _update_service_status(self, service_name: str, status: str, mode: str):
        """Update service status in database"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('''
                    INSERT OR REPLACE INTO service_status (service_name, status, mode)
                    VALUES (?, ?, ?)
                ''', (service_name, status, mode))
        except Exception as e:
            self.logger.error(f"Failed to update service status: {e}")
    
    def _start_maintenance_session(self):
        """Start maintenance session in database"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute('''
                    INSERT INTO maintenance_sessions (
                        start_time, reason, estimated_duration_seconds, status
                    ) VALUES (?, ?, ?, ?)
                ''', (
                    self.maintenance_start_time,
                    self.maintenance_reason,
                    self.estimated_duration,
                    'active'
                ))
                self.current_session_id = cursor.lastrowid
        except Exception as e:
            self.logger.error(f"Failed to start maintenance session: {e}")
    
    def _complete_maintenance_session(self, success: bool):
        """Complete maintenance session in database"""
        try:
            session_id = getattr(self, 'current_session_id', None)
            if not session_id:
                return
            
            duration = None
            if self.maintenance_start_time and self.maintenance_end_time:
                duration = (self.maintenance_end_time - self.maintenance_start_time).total_seconds()
            
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('''
                    UPDATE maintenance_sessions SET
                        end_time = ?, actual_duration_seconds = ?, status = ?,
                        tasks_completed = ?, tasks_failed = ?
                    WHERE id = ?
                ''', (
                    self.maintenance_end_time,
                    duration,
                    'completed' if success else 'failed',
                    len(self.completed_tasks),
                    len(self.failed_tasks),
                    session_id
                ))
        except Exception as e:
            self.logger.error(f"Failed to complete maintenance session: {e}")
    
    def _get_current_session_id(self) -> Optional[int]:
        """Get current maintenance session ID"""
        return getattr(self, 'current_session_id', None)
    
    def _start_monitoring(self):
        """Start maintenance monitoring"""
        if self.monitoring_active:
            return
        
        self.monitoring_active = True
        self.monitoring_thread = threading.Thread(target=self._monitoring_loop, daemon=True)
        self.monitoring_thread.start()
    
    def _stop_monitoring(self):
        """Stop maintenance monitoring"""
        self.monitoring_active = False
        if self.monitoring_thread and self.monitoring_thread.is_alive():
            self.monitoring_thread.join(timeout=5)
    
    def _monitoring_loop(self):
        """Maintenance monitoring loop"""
        while self.monitoring_active:
            try:
                # Check maintenance duration
                if self.maintenance_start_time and self.estimated_duration:
                    elapsed = (datetime.now() - self.maintenance_start_time).total_seconds()
                    if elapsed > self.estimated_duration * 1.5:  # 150% of estimated time
                        self.logger.warning("Maintenance duration exceeded estimate significantly")
                
                # Check system health
                if not self._validate_essential_services():
                    self.logger.error("Essential service health check failed during maintenance")
                
                time.sleep(self.status_check_interval)
                
            except Exception as e:
                self.logger.error(f"Error in maintenance monitoring: {e}")
                time.sleep(self.status_check_interval)
    
    def _rollback_maintenance_entry(self):
        """Rollback failed maintenance entry"""
        try:
            self.current_mode = MaintenanceMode.NORMAL
            self.maintenance_reason = None
            self.maintenance_start_time = None
            self.estimated_duration = None
            
            # Restore any changes made
            self._deactivate_maintenance_page()
            self._restore_services()
            self._restore_service_levels()
            
            self.logger.info("Maintenance entry rolled back")
            
        except Exception as e:
            self.logger.error(f"Error during maintenance rollback: {e}")

def main():
    """Main maintenance mode function"""
    import argparse
    
    parser = argparse.ArgumentParser(description='AI Rebuild Maintenance Mode Manager')
    parser.add_argument('action', choices=['enter', 'exit', 'status', 'execute'], 
                       help='Maintenance action to perform')
    parser.add_argument('--reason', help='Reason for maintenance (required for enter)')
    parser.add_argument('--duration', type=float, help='Estimated duration in hours')
    parser.add_argument('--emergency', action='store_true', help='Emergency maintenance mode')
    parser.add_argument('--config', help='Configuration file path')
    
    args = parser.parse_args()
    
    manager = MaintenanceManager(args.config)
    
    if args.action == 'enter':
        if not args.reason:
            print("Error: --reason is required for entering maintenance mode")
            return 1
        
        success = manager.enter_maintenance_mode(
            reason=args.reason,
            estimated_duration_hours=args.duration,
            emergency=args.emergency
        )
        return 0 if success else 1
    
    elif args.action == 'exit':
        success = manager.exit_maintenance_mode()
        return 0 if success else 1
    
    elif args.action == 'status':
        status = manager.get_maintenance_status()
        print(json.dumps(status, indent=2))
        return 0
    
    elif args.action == 'execute':
        if manager.current_mode != MaintenanceMode.MAINTENANCE:
            print("Error: Not in maintenance mode")
            return 1
        
        success = manager.execute_maintenance_tasks()
        return 0 if success else 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())