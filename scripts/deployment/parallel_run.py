#!/usr/bin/env python3
"""
Parallel Run Environment Setup for AI Rebuild Migration
Sets up and manages parallel running of old and new systems during migration.
"""

import json
import logging
import os
import subprocess
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
import psutil

@dataclass
class ParallelRunConfig:
    """Configuration for parallel run setup"""
    old_system_dir: str
    new_system_dir: str
    parallel_env_dir: str
    old_system_port: int = 8000
    new_system_port: int = 8001
    monitoring_port: int = 8002
    data_sync_interval: int = 300  # 5 minutes
    health_check_interval: int = 60  # 1 minute
    max_parallel_duration: int = 86400  # 24 hours
    enable_data_sync: bool = True
    enable_traffic_mirroring: bool = False

class ParallelRunManager:
    """Main parallel run management handler"""
    
    def __init__(self, config: ParallelRunConfig):
        self.config = config
        self.parallel_env_dir = Path(config.parallel_env_dir)
        self.parallel_env_dir.mkdir(parents=True, exist_ok=True)
        
        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(self.parallel_env_dir / 'parallel_run.log'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
        
        # System tracking
        self.system_processes = {
            'old_system': None,
            'new_system': None,
            'monitoring': None
        }
        
        self.system_status = {
            'old_system': {'status': 'stopped', 'health': 'unknown', 'last_check': None},
            'new_system': {'status': 'stopped', 'health': 'unknown', 'last_check': None},
            'parallel_run': {'started_at': None, 'duration': 0, 'status': 'not_started'}
        }
        
        # Monitoring data
        self.monitoring_data = {
            'start_time': None,
            'metrics': [],
            'health_checks': [],
            'data_sync_logs': [],
            'traffic_metrics': []
        }
        
        # Threading for background tasks
        self.monitoring_thread = None
        self.data_sync_thread = None
        self.shutdown_event = threading.Event()
    
    def setup_parallel_environment(self) -> Dict[str, Any]:
        """Setup parallel running environment"""
        self.logger.info("Setting up parallel running environment")
        
        try:
            # Validate system directories
            self._validate_system_directories()
            
            # Setup environment directories
            self._setup_environment_directories()
            
            # Configure systems for parallel run
            self._configure_systems()
            
            # Setup monitoring infrastructure
            self._setup_monitoring()
            
            # Prepare data synchronization
            if self.config.enable_data_sync:
                self._prepare_data_sync()
            
            self.logger.info("Parallel environment setup completed")
            
            return {
                'setup_completed': True,
                'old_system_port': self.config.old_system_port,
                'new_system_port': self.config.new_system_port,
                'monitoring_port': self.config.monitoring_port,
                'environment_dir': str(self.parallel_env_dir)
            }
            
        except Exception as e:
            self.logger.error(f"Failed to setup parallel environment: {str(e)}")
            raise
    
    def start_parallel_run(self) -> Dict[str, Any]:
        """Start parallel running of both systems"""
        self.logger.info("Starting parallel run")
        
        try:
            # Start old system
            self._start_old_system()
            
            # Start new system
            self._start_new_system()
            
            # Start monitoring
            self._start_monitoring()
            
            # Start background tasks
            self._start_background_tasks()
            
            # Update status
            self.system_status['parallel_run'] = {
                'started_at': datetime.utcnow().isoformat(),
                'duration': 0,
                'status': 'running'
            }
            
            self.monitoring_data['start_time'] = datetime.utcnow()
            
            self.logger.info("Parallel run started successfully")
            
            return {
                'parallel_run_started': True,
                'started_at': self.system_status['parallel_run']['started_at'],
                'old_system_url': f"http://localhost:{self.config.old_system_port}",
                'new_system_url': f"http://localhost:{self.config.new_system_port}",
                'monitoring_url': f"http://localhost:{self.config.monitoring_port}"
            }
            
        except Exception as e:
            self.logger.error(f"Failed to start parallel run: {str(e)}")
            # Cleanup any started processes
            self.stop_parallel_run()
            raise
    
    def stop_parallel_run(self) -> Dict[str, Any]:
        """Stop parallel running and cleanup"""
        self.logger.info("Stopping parallel run")
        
        try:
            # Signal shutdown
            self.shutdown_event.set()
            
            # Stop background tasks
            self._stop_background_tasks()
            
            # Stop systems
            self._stop_system('monitoring')
            self._stop_system('new_system')
            self._stop_system('old_system')
            
            # Generate final report
            final_report = self._generate_final_report()
            
            # Update status
            self.system_status['parallel_run']['status'] = 'stopped'
            
            self.logger.info("Parallel run stopped successfully")
            
            return final_report
            
        except Exception as e:
            self.logger.error(f"Error stopping parallel run: {str(e)}")
            raise
    
    def get_status(self) -> Dict[str, Any]:
        """Get current parallel run status"""
        # Update duration if running
        if self.system_status['parallel_run']['status'] == 'running':
            start_time = datetime.fromisoformat(self.system_status['parallel_run']['started_at'])
            duration = (datetime.utcnow() - start_time).total_seconds()
            self.system_status['parallel_run']['duration'] = duration
        
        return {
            'timestamp': datetime.utcnow().isoformat(),
            'system_status': self.system_status,
            'resource_usage': self._get_resource_usage(),
            'recent_metrics': self.monitoring_data['metrics'][-10:] if self.monitoring_data['metrics'] else [],
            'health_summary': self._get_health_summary()
        }
    
    def _validate_system_directories(self):
        """Validate that system directories exist and are valid"""
        old_system_path = Path(self.config.old_system_dir)
        new_system_path = Path(self.config.new_system_dir)
        
        if not old_system_path.exists():
            raise FileNotFoundError(f"Old system directory not found: {old_system_path}")
        
        if not new_system_path.exists():
            raise FileNotFoundError(f"New system directory not found: {new_system_path}")
        
        # Check for required files/scripts
        self._validate_system_structure(old_system_path, "old system")
        self._validate_system_structure(new_system_path, "new system")
    
    def _validate_system_structure(self, system_path: Path, system_name: str):
        """Validate system directory structure"""
        required_items = ['scripts', 'config']
        
        for item in required_items:
            item_path = system_path / item
            if not item_path.exists():
                self.logger.warning(f"{system_name} missing {item} directory: {item_path}")
    
    def _setup_environment_directories(self):
        """Setup environment directory structure"""
        directories = [
            'logs', 'configs', 'data', 'monitoring', 'scripts', 'temp'
        ]
        
        for directory in directories:
            dir_path = self.parallel_env_dir / directory
            dir_path.mkdir(exist_ok=True)
    
    def _configure_systems(self):
        """Configure both systems for parallel running"""
        self.logger.info("Configuring systems for parallel run")
        
        # Configure old system
        self._configure_old_system()
        
        # Configure new system
        self._configure_new_system()
    
    def _configure_old_system(self):
        """Configure old system for parallel running"""
        old_config_dir = self.parallel_env_dir / 'configs' / 'old_system'
        old_config_dir.mkdir(exist_ok=True)
        
        # Create old system configuration
        old_system_config = {
            'server': {
                'port': self.config.old_system_port,
                'host': '127.0.0.1',
                'debug': False
            },
            'database': {
                'name': 'ai_rebuild_old',
                'backup_enabled': True
            },
            'logging': {
                'level': 'INFO',
                'file': str(self.parallel_env_dir / 'logs' / 'old_system.log')
            },
            'parallel_run': {
                'enabled': True,
                'system_id': 'old_system',
                'monitoring_enabled': True
            }
        }
        
        with open(old_config_dir / 'config.json', 'w') as f:
            json.dump(old_system_config, f, indent=2)
        
        # Create startup script for old system
        self._create_system_startup_script('old_system', self.config.old_system_dir, old_config_dir)
    
    def _configure_new_system(self):
        """Configure new system for parallel running"""
        new_config_dir = self.parallel_env_dir / 'configs' / 'new_system'
        new_config_dir.mkdir(exist_ok=True)
        
        # Create new system configuration
        new_system_config = {
            'server': {
                'port': self.config.new_system_port,
                'host': '127.0.0.1',
                'debug': False
            },
            'database': {
                'name': 'ai_rebuild_new',
                'backup_enabled': True
            },
            'logging': {
                'level': 'INFO',
                'file': str(self.parallel_env_dir / 'logs' / 'new_system.log')
            },
            'parallel_run': {
                'enabled': True,
                'system_id': 'new_system',
                'monitoring_enabled': True,
                'data_sync_enabled': self.config.enable_data_sync
            }
        }
        
        with open(new_config_dir / 'config.json', 'w') as f:
            json.dump(new_system_config, f, indent=2)
        
        # Create startup script for new system
        self._create_system_startup_script('new_system', self.config.new_system_dir, new_config_dir)
    
    def _create_system_startup_script(self, system_name: str, system_dir: str, config_dir: Path):
        """Create startup script for a system"""
        script_path = self.parallel_env_dir / 'scripts' / f'start_{system_name}.py'
        
        script_content = f'''#!/usr/bin/env python3
"""
Startup script for {system_name} in parallel run environment
"""

import os
import sys
from pathlib import Path

def start_{system_name}():
    """Start {system_name}"""
    system_dir = Path("{system_dir}")
    config_file = Path("{config_dir}") / "config.json"
    
    # Change to system directory
    os.chdir(system_dir)
    
    # Set environment variables
    os.environ['AI_REBUILD_CONFIG'] = str(config_file)
    os.environ['AI_REBUILD_PARALLEL_MODE'] = 'true'
    
    # Start the system
    if (system_dir / "scripts" / "startup.py").exists():
        os.system(f"python scripts/startup.py --config {{config_file}}")
    else:
        print(f"Warning: startup script not found for {system_name}")
        # Fallback to basic Python server
        port = {self.config.old_system_port if system_name == 'old_system' else self.config.new_system_port}
        os.system(f"python -m http.server {{port}}")

if __name__ == "__main__":
    start_{system_name}()
'''
        
        with open(script_path, 'w') as f:
            f.write(script_content)
        
        script_path.chmod(0o755)
    
    def _setup_monitoring(self):
        """Setup monitoring infrastructure"""
        monitoring_dir = self.parallel_env_dir / 'monitoring'
        
        # Create monitoring dashboard script
        dashboard_script = monitoring_dir / 'dashboard.py'
        
        dashboard_content = f'''#!/usr/bin/env python3
"""
Parallel Run Monitoring Dashboard
"""

import json
import time
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler
import threading

class MonitoringHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/status':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            
            # Read current status
            try:
                status_file = Path("{self.parallel_env_dir}") / "status.json"
                if status_file.exists():
                    with open(status_file, 'r') as f:
                        status = json.load(f)
                else:
                    status = {{"error": "Status file not found"}}
                
                self.wfile.write(json.dumps(status).encode())
            except Exception as e:
                self.wfile.write(json.dumps({{"error": str(e)}}).encode())
        
        elif self.path == '/' or self.path == '/dashboard':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            
            html = """
            <!DOCTYPE html>
            <html>
            <head>
                <title>AI Rebuild - Parallel Run Dashboard</title>
                <style>
                    body {{ font-family: Arial, sans-serif; margin: 20px; }}
                    .status {{ margin: 10px 0; padding: 10px; border-radius: 5px; }}
                    .running {{ background-color: #d4edda; }}
                    .stopped {{ background-color: #f8d7da; }}
                    .warning {{ background-color: #fff3cd; }}
                </style>
            </head>
            <body>
                <h1>AI Rebuild - Parallel Run Dashboard</h1>
                <div id="status">Loading...</div>
                
                <script>
                    function updateStatus() {{
                        fetch('/status')
                            .then(response => response.json())
                            .then(data => {{
                                document.getElementById('status').innerHTML = 
                                    '<pre>' + JSON.stringify(data, null, 2) + '</pre>';
                            }});
                    }}
                    
                    updateStatus();
                    setInterval(updateStatus, 5000);
                </script>
            </body>
            </html>
            """
            
            self.wfile.write(html.encode())
        else:
            super().do_GET()

def start_monitoring():
    """Start monitoring dashboard"""
    server = HTTPServer(('localhost', {self.config.monitoring_port}), MonitoringHandler)
    server.serve_forever()

if __name__ == "__main__":
    start_monitoring()
'''
        
        with open(dashboard_script, 'w') as f:
            f.write(dashboard_content)
        
        dashboard_script.chmod(0o755)
    
    def _prepare_data_sync(self):
        """Prepare data synchronization infrastructure"""
        if not self.config.enable_data_sync:
            return
        
        self.logger.info("Preparing data synchronization")
        
        sync_dir = self.parallel_env_dir / 'data' / 'sync'
        sync_dir.mkdir(exist_ok=True)
        
        # Create data sync script
        sync_script = self.parallel_env_dir / 'scripts' / 'data_sync.py'
        
        sync_content = f'''#!/usr/bin/env python3
"""
Data synchronization between old and new systems
"""

import json
import time
import sqlite3
from datetime import datetime
from pathlib import Path

def sync_data():
    """Synchronize data between systems"""
    sync_log = []
    
    try:
        # This would contain actual data sync logic
        # For now, just log the sync attempt
        sync_entry = {{
            'timestamp': datetime.utcnow().isoformat(),
            'status': 'completed',
            'records_synced': 0,
            'sync_type': 'incremental'
        }}
        
        sync_log.append(sync_entry)
        
        # Save sync log
        log_file = Path("{sync_dir}") / "sync_log.json"
        with open(log_file, 'w') as f:
            json.dump(sync_log, f, indent=2)
        
        print(f"Data sync completed: {{sync_entry}}")
        return True
        
    except Exception as e:
        sync_entry = {{
            'timestamp': datetime.utcnow().isoformat(),
            'status': 'failed',
            'error': str(e)
        }}
        sync_log.append(sync_entry)
        print(f"Data sync failed: {{sync_entry}}")
        return False

if __name__ == "__main__":
    sync_data()
'''
        
        with open(sync_script, 'w') as f:
            f.write(sync_content)
        
        sync_script.chmod(0o755)
    
    def _start_old_system(self):
        """Start the old system"""
        self.logger.info("Starting old system")
        
        script_path = self.parallel_env_dir / 'scripts' / 'start_old_system.py'
        
        try:
            process = subprocess.Popen(
                ['python', str(script_path)],
                cwd=self.config.old_system_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            self.system_processes['old_system'] = process
            self.system_status['old_system']['status'] = 'starting'
            
            # Wait a moment and check if process started successfully
            time.sleep(2)
            if process.poll() is None:
                self.system_status['old_system']['status'] = 'running'
                self.logger.info(f"Old system started (PID: {process.pid})")
            else:
                raise RuntimeError("Old system failed to start")
            
        except Exception as e:
            self.logger.error(f"Failed to start old system: {str(e)}")
            self.system_status['old_system']['status'] = 'failed'
            raise
    
    def _start_new_system(self):
        """Start the new system"""
        self.logger.info("Starting new system")
        
        script_path = self.parallel_env_dir / 'scripts' / 'start_new_system.py'
        
        try:
            process = subprocess.Popen(
                ['python', str(script_path)],
                cwd=self.config.new_system_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            self.system_processes['new_system'] = process
            self.system_status['new_system']['status'] = 'starting'
            
            # Wait a moment and check if process started successfully
            time.sleep(2)
            if process.poll() is None:
                self.system_status['new_system']['status'] = 'running'
                self.logger.info(f"New system started (PID: {process.pid})")
            else:
                raise RuntimeError("New system failed to start")
            
        except Exception as e:
            self.logger.error(f"Failed to start new system: {str(e)}")
            self.system_status['new_system']['status'] = 'failed'
            raise
    
    def _start_monitoring(self):
        """Start the monitoring dashboard"""
        self.logger.info("Starting monitoring dashboard")
        
        dashboard_script = self.parallel_env_dir / 'monitoring' / 'dashboard.py'
        
        try:
            process = subprocess.Popen(
                ['python', str(dashboard_script)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            self.system_processes['monitoring'] = process
            
            # Wait a moment and check if monitoring started
            time.sleep(1)
            if process.poll() is None:
                self.logger.info(f"Monitoring dashboard started on port {self.config.monitoring_port}")
            else:
                self.logger.warning("Monitoring dashboard may have failed to start")
            
        except Exception as e:
            self.logger.error(f"Failed to start monitoring: {str(e)}")
    
    def _start_background_tasks(self):
        """Start background monitoring and sync tasks"""
        self.logger.info("Starting background tasks")
        
        # Start monitoring thread
        self.monitoring_thread = threading.Thread(target=self._monitoring_worker)
        self.monitoring_thread.daemon = True
        self.monitoring_thread.start()
        
        # Start data sync thread if enabled
        if self.config.enable_data_sync:
            self.data_sync_thread = threading.Thread(target=self._data_sync_worker)
            self.data_sync_thread.daemon = True
            self.data_sync_thread.start()
    
    def _monitoring_worker(self):
        """Background monitoring worker"""
        self.logger.info("Monitoring worker started")
        
        while not self.shutdown_event.is_set():
            try:
                # Perform health checks
                self._perform_health_checks()
                
                # Collect metrics
                self._collect_metrics()
                
                # Update status file
                self._update_status_file()
                
                # Check for automatic shutdown conditions
                self._check_auto_shutdown()
                
            except Exception as e:
                self.logger.error(f"Error in monitoring worker: {str(e)}")
            
            # Wait before next iteration
            self.shutdown_event.wait(self.config.health_check_interval)
    
    def _data_sync_worker(self):
        """Background data synchronization worker"""
        self.logger.info("Data sync worker started")
        
        while not self.shutdown_event.is_set():
            try:
                # Run data sync script
                sync_script = self.parallel_env_dir / 'scripts' / 'data_sync.py'
                result = subprocess.run(
                    ['python', str(sync_script)],
                    capture_output=True,
                    text=True
                )
                
                sync_log = {
                    'timestamp': datetime.utcnow().isoformat(),
                    'return_code': result.returncode,
                    'stdout': result.stdout,
                    'stderr': result.stderr
                }
                
                self.monitoring_data['data_sync_logs'].append(sync_log)
                
                # Keep only last 100 sync logs
                if len(self.monitoring_data['data_sync_logs']) > 100:
                    self.monitoring_data['data_sync_logs'] = self.monitoring_data['data_sync_logs'][-100:]
                
            except Exception as e:
                self.logger.error(f"Error in data sync worker: {str(e)}")
            
            # Wait before next sync
            self.shutdown_event.wait(self.config.data_sync_interval)
    
    def _perform_health_checks(self):
        """Perform health checks on both systems"""
        health_check = {
            'timestamp': datetime.utcnow().isoformat(),
            'old_system': self._check_system_health('old_system', self.config.old_system_port),
            'new_system': self._check_system_health('new_system', self.config.new_system_port)
        }
        
        self.monitoring_data['health_checks'].append(health_check)
        
        # Update system status
        self.system_status['old_system']['health'] = health_check['old_system']['status']
        self.system_status['old_system']['last_check'] = health_check['timestamp']
        self.system_status['new_system']['health'] = health_check['new_system']['status']
        self.system_status['new_system']['last_check'] = health_check['timestamp']
        
        # Keep only last 100 health checks
        if len(self.monitoring_data['health_checks']) > 100:
            self.monitoring_data['health_checks'] = self.monitoring_data['health_checks'][-100:]
    
    def _check_system_health(self, system_name: str, port: int) -> Dict[str, Any]:
        """Check health of a specific system"""
        health_result = {
            'status': 'unknown',
            'response_time': None,
            'error': None
        }
        
        try:
            import requests
            
            start_time = time.time()
            response = requests.get(f"http://localhost:{port}/health", timeout=5)
            response_time = time.time() - start_time
            
            if response.status_code == 200:
                health_result['status'] = 'healthy'
                health_result['response_time'] = response_time
            else:
                health_result['status'] = 'unhealthy'
                health_result['error'] = f"HTTP {response.status_code}"
        
        except requests.exceptions.ConnectionError:
            health_result['status'] = 'unreachable'
            health_result['error'] = 'Connection refused'
        except requests.exceptions.Timeout:
            health_result['status'] = 'timeout'
            health_result['error'] = 'Request timeout'
        except Exception as e:
            health_result['status'] = 'error'
            health_result['error'] = str(e)
        
        return health_result
    
    def _collect_metrics(self):
        """Collect system metrics"""
        metrics = {
            'timestamp': datetime.utcnow().isoformat(),
            'system_resources': self._get_resource_usage(),
            'process_status': self._get_process_status()
        }
        
        self.monitoring_data['metrics'].append(metrics)
        
        # Keep only last 1000 metric points
        if len(self.monitoring_data['metrics']) > 1000:
            self.monitoring_data['metrics'] = self.monitoring_data['metrics'][-1000:]
    
    def _get_resource_usage(self) -> Dict[str, Any]:
        """Get system resource usage"""
        try:
            return {
                'cpu_percent': psutil.cpu_percent(),
                'memory_percent': psutil.virtual_memory().percent,
                'disk_usage_percent': psutil.disk_usage('/').percent,
                'load_average': os.getloadavg() if hasattr(os, 'getloadavg') else None
            }
        except Exception as e:
            return {'error': str(e)}
    
    def _get_process_status(self) -> Dict[str, Any]:
        """Get status of system processes"""
        process_status = {}
        
        for system_name, process in self.system_processes.items():
            if process:
                try:
                    if process.poll() is None:  # Process is running
                        proc = psutil.Process(process.pid)
                        process_status[system_name] = {
                            'pid': process.pid,
                            'status': 'running',
                            'cpu_percent': proc.cpu_percent(),
                            'memory_mb': proc.memory_info().rss / 1024 / 1024,
                            'create_time': proc.create_time()
                        }
                    else:
                        process_status[system_name] = {
                            'pid': process.pid,
                            'status': 'stopped',
                            'return_code': process.poll()
                        }
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    process_status[system_name] = {
                        'pid': process.pid,
                        'status': 'unknown',
                        'error': 'Process not accessible'
                    }
            else:
                process_status[system_name] = {'status': 'not_started'}
        
        return process_status
    
    def _update_status_file(self):
        """Update status file for monitoring dashboard"""
        status_data = {
            'timestamp': datetime.utcnow().isoformat(),
            'parallel_run_status': self.system_status,
            'latest_metrics': self.monitoring_data['metrics'][-1] if self.monitoring_data['metrics'] else {},
            'latest_health': self.monitoring_data['health_checks'][-1] if self.monitoring_data['health_checks'] else {},
            'uptime_seconds': (datetime.utcnow() - self.monitoring_data['start_time']).total_seconds() if self.monitoring_data['start_time'] else 0
        }
        
        status_file = self.parallel_env_dir / 'status.json'
        with open(status_file, 'w') as f:
            json.dump(status_data, f, indent=2, default=str)
    
    def _check_auto_shutdown(self):
        """Check if automatic shutdown conditions are met"""
        if self.monitoring_data['start_time']:
            runtime = (datetime.utcnow() - self.monitoring_data['start_time']).total_seconds()
            
            if runtime > self.config.max_parallel_duration:
                self.logger.warning(f"Maximum parallel run duration reached ({self.config.max_parallel_duration}s)")
                # Could trigger automatic shutdown here
    
    def _get_health_summary(self) -> Dict[str, Any]:
        """Get health summary for both systems"""
        if not self.monitoring_data['health_checks']:
            return {'status': 'no_data'}
        
        latest_health = self.monitoring_data['health_checks'][-1]
        
        return {
            'overall_status': 'healthy' if (
                latest_health['old_system']['status'] == 'healthy' and
                latest_health['new_system']['status'] == 'healthy'
            ) else 'degraded',
            'old_system_status': latest_health['old_system']['status'],
            'new_system_status': latest_health['new_system']['status'],
            'last_check': latest_health['timestamp']
        }
    
    def _stop_background_tasks(self):
        """Stop background tasks"""
        self.logger.info("Stopping background tasks")
        
        # Signal shutdown and wait for threads
        self.shutdown_event.set()
        
        if self.monitoring_thread and self.monitoring_thread.is_alive():
            self.monitoring_thread.join(timeout=5)
        
        if self.data_sync_thread and self.data_sync_thread.is_alive():
            self.data_sync_thread.join(timeout=5)
    
    def _stop_system(self, system_name: str):
        """Stop a specific system"""
        process = self.system_processes.get(system_name)
        
        if process and process.poll() is None:
            self.logger.info(f"Stopping {system_name}")
            
            try:
                # Try graceful shutdown first
                process.terminate()
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                # Force kill if graceful shutdown fails
                self.logger.warning(f"Force killing {system_name}")
                process.kill()
                process.wait()
            
            self.system_status[system_name]['status'] = 'stopped'
    
    def _generate_final_report(self) -> Dict[str, Any]:
        """Generate final parallel run report"""
        if not self.monitoring_data['start_time']:
            return {'error': 'No monitoring data available'}
        
        total_runtime = (datetime.utcnow() - self.monitoring_data['start_time']).total_seconds()
        
        # Calculate statistics
        health_checks = self.monitoring_data['health_checks']
        old_system_healthy = sum(1 for hc in health_checks if hc['old_system']['status'] == 'healthy')
        new_system_healthy = sum(1 for hc in health_checks if hc['new_system']['status'] == 'healthy')
        
        report = {
            'parallel_run_summary': {
                'started_at': self.monitoring_data['start_time'].isoformat(),
                'stopped_at': datetime.utcnow().isoformat(),
                'total_runtime_seconds': total_runtime,
                'total_runtime_hours': total_runtime / 3600
            },
            'health_statistics': {
                'total_health_checks': len(health_checks),
                'old_system_healthy_percent': (old_system_healthy / len(health_checks) * 100) if health_checks else 0,
                'new_system_healthy_percent': (new_system_healthy / len(health_checks) * 100) if health_checks else 0
            },
            'data_sync_statistics': {
                'total_sync_attempts': len(self.monitoring_data['data_sync_logs']),
                'successful_syncs': sum(1 for log in self.monitoring_data['data_sync_logs'] if log['return_code'] == 0)
            },
            'final_status': self.system_status
        }
        
        # Save final report
        report_file = self.parallel_env_dir / 'final_parallel_run_report.json'
        with open(report_file, 'w') as f:
            json.dump(report, f, indent=2, default=str)
        
        return report

def main():
    """Main entry point"""
    # Default configuration
    config = ParallelRunConfig(
        old_system_dir=str(Path.cwd()),
        new_system_dir=str(Path.cwd()),
        parallel_env_dir=str(Path.cwd() / 'parallel_run_environment'),
        old_system_port=8000,
        new_system_port=8001,
        monitoring_port=8002,
        enable_data_sync=True
    )
    
    # Create parallel run manager
    manager = ParallelRunManager(config)
    
    try:
        # Setup environment
        setup_result = manager.setup_parallel_environment()
        print("Parallel environment setup completed:")
        print(json.dumps(setup_result, indent=2))
        
        # Start parallel run
        start_result = manager.start_parallel_run()
        print("\\nParallel run started:")
        print(json.dumps(start_result, indent=2))
        
        print(f"\\nMonitoring dashboard: http://localhost:{config.monitoring_port}")
        print("Press Ctrl+C to stop parallel run...")
        
        # Keep running until interrupted
        while True:
            time.sleep(60)
            status = manager.get_status()
            print(f"Status: {status['health_summary']}")
    
    except KeyboardInterrupt:
        print("\\nShutting down parallel run...")
        final_report = manager.stop_parallel_run()
        print("Final report:")
        print(json.dumps(final_report, indent=2))
    
    except Exception as e:
        print(f"Error: {str(e)}")
        manager.stop_parallel_run()

if __name__ == "__main__":
    main()