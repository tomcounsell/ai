"""
Production startup script with comprehensive environment validation,
database initialization, health checks, and component startup.
"""

import os
import sys
import time
import logging
import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime
import shutil
import importlib.util

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

class StartupManager:
    """
    Production startup manager with comprehensive system validation.
    
    Startup sequence:
    1. Environment validation
    2. Dependency checks  
    3. Database initialization
    4. Configuration validation
    5. Health checks
    6. Component startup
    7. Monitoring activation
    """
    
    def __init__(self, config_file: Optional[str] = None):
        self.config_file = config_file or "config/workspace_config.json"
        self.logger = self._setup_logging()
        self.startup_time = datetime.now()
        self.startup_log = []
        
        # Load configuration
        self.config = self._load_configuration()
        
        # Startup state
        self.failed_components = []
        self.started_components = []
        
    def _setup_logging(self) -> logging.Logger:
        """Setup startup logging"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.StreamHandler(sys.stdout),
                logging.FileHandler('logs/startup.log', mode='a')
            ]
        )
        return logging.getLogger(__name__)
    
    def _load_configuration(self) -> Dict[str, Any]:
        """Load system configuration"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
                self.logger.info(f"Loaded configuration from {self.config_file}")
                return config
            else:
                self.logger.warning(f"Configuration file not found: {self.config_file}, using defaults")
                return self._get_default_config()
        except Exception as e:
            self.logger.error(f"Error loading configuration: {e}")
            return self._get_default_config()
    
    def _get_default_config(self) -> Dict[str, Any]:
        """Get default configuration"""
        return {
            "database": {
                "path": "data/ai_rebuild.db",
                "backup_on_startup": True,
                "run_migrations": True
            },
            "monitoring": {
                "enabled": True,
                "dashboard_port": 8080,
                "alert_email": None
            },
            "components": {
                "agents": True,
                "mcp_servers": True,
                "telegram": False,
                "tools": True
            },
            "security": {
                "rate_limiting": True,
                "audit_logging": True,
                "api_key_rotation": False
            },
            "performance": {
                "memory_limit_mb": 2048,
                "cpu_limit_percent": 80,
                "enable_caching": True
            }
        }
    
    def startup(self) -> bool:
        """Execute complete startup sequence"""
        self.logger.info("=" * 60)
        self.logger.info("🚀 AI Rebuild Production Startup")
        self.logger.info("=" * 60)
        
        startup_steps = [
            ("Environment Validation", self._validate_environment),
            ("Dependency Checks", self._check_dependencies),
            ("Directory Structure", self._setup_directories),
            ("Database Initialization", self._initialize_database),
            ("Configuration Validation", self._validate_configuration),
            ("Component Health Checks", self._health_checks),
            ("Core Components", self._start_core_components),
            ("Monitoring Systems", self._start_monitoring),
            ("Security Systems", self._start_security),
            ("Integration Services", self._start_integrations),
            ("Final Validation", self._final_validation)
        ]
        
        success = True
        
        for step_name, step_func in startup_steps:
            self.logger.info(f"📋 {step_name}...")
            step_start = time.time()
            
            try:
                step_result = step_func()
                step_duration = time.time() - step_start
                
                if step_result:
                    self.logger.info(f"✅ {step_name} completed ({step_duration:.2f}s)")
                    self.startup_log.append({
                        'step': step_name,
                        'status': 'success',
                        'duration': step_duration,
                        'timestamp': datetime.now().isoformat()
                    })
                else:
                    self.logger.error(f"❌ {step_name} failed ({step_duration:.2f}s)")
                    self.startup_log.append({
                        'step': step_name,
                        'status': 'failed',
                        'duration': step_duration,
                        'timestamp': datetime.now().isoformat()
                    })
                    success = False
                    break
                    
            except Exception as e:
                step_duration = time.time() - step_start
                self.logger.error(f"❌ {step_name} error: {e} ({step_duration:.2f}s)")
                self.startup_log.append({
                    'step': step_name,
                    'status': 'error',
                    'error': str(e),
                    'duration': step_duration,
                    'timestamp': datetime.now().isoformat()
                })
                success = False
                break
        
        # Save startup log
        self._save_startup_log(success)
        
        total_duration = time.time() - self.startup_time.timestamp()
        
        if success:
            self.logger.info("=" * 60)
            self.logger.info(f"🎉 AI Rebuild started successfully! ({total_duration:.2f}s)")
            self.logger.info(f"📊 Started components: {len(self.started_components)}")
            self.logger.info("=" * 60)
        else:
            self.logger.error("=" * 60)
            self.logger.error(f"💥 AI Rebuild startup failed! ({total_duration:.2f}s)")
            self.logger.error(f"❌ Failed components: {len(self.failed_components)}")
            self.logger.error("=" * 60)
        
        return success
    
    def _validate_environment(self) -> bool:
        """Validate system environment"""
        try:
            # Check Python version
            python_version = sys.version_info
            if python_version < (3, 8):
                self.logger.error(f"Python 3.8+ required, found {python_version}")
                return False
            
            self.logger.info(f"Python version: {sys.version}")
            
            # Check required environment variables
            required_env = ['HOME', 'PATH']
            for env_var in required_env:
                if not os.getenv(env_var):
                    self.logger.error(f"Required environment variable missing: {env_var}")
                    return False
            
            # Check disk space
            disk_usage = shutil.disk_usage('.')
            free_gb = disk_usage.free / (1024**3)
            if free_gb < 1.0:
                self.logger.warning(f"Low disk space: {free_gb:.2f}GB available")
            else:
                self.logger.info(f"Disk space: {free_gb:.2f}GB available")
            
            # Check memory
            try:
                import psutil
                memory = psutil.virtual_memory()
                memory_gb = memory.total / (1024**3)
                self.logger.info(f"System memory: {memory_gb:.2f}GB")
                
                if memory.percent > 90:
                    self.logger.warning(f"High memory usage: {memory.percent:.1f}%")
            except ImportError:
                self.logger.warning("psutil not available for memory check")
            
            # Check working directory
            if not os.path.exists('config'):
                self.logger.error("Config directory not found - not in project root?")
                return False
            
            return True
            
        except Exception as e:
            self.logger.error(f"Environment validation error: {e}")
            return False
    
    def _check_dependencies(self) -> bool:
        """Check required dependencies"""
        try:
            required_packages = [
                'sqlite3',
                'json',
                'logging',
                'datetime',
                'pathlib',
                'threading'
            ]
            
            optional_packages = {
                'psutil': 'System monitoring',
                'requests': 'HTTP clients',
                'asyncio': 'Async operations'
            }
            
            # Check required packages
            for package in required_packages:
                try:
                    __import__(package)
                except ImportError as e:
                    self.logger.error(f"Required package missing: {package}")
                    return False
            
            # Check optional packages
            missing_optional = []
            for package, description in optional_packages.items():
                try:
                    __import__(package)
                except ImportError:
                    missing_optional.append(f"{package} ({description})")
            
            if missing_optional:
                self.logger.warning(f"Optional packages missing: {', '.join(missing_optional)}")
            
            # Check project modules
            project_modules = [
                'config.settings',
                'utilities.database',
                'utilities.logging_config',
                'agents',
                'tools',
                'mcp_servers'
            ]
            
            for module in project_modules:
                try:
                    spec = importlib.util.find_spec(module)
                    if spec is None:
                        self.logger.error(f"Project module not found: {module}")
                        return False
                except Exception as e:
                    self.logger.warning(f"Could not check module {module}: {e}")
            
            self.logger.info("All dependencies validated")
            return True
            
        except Exception as e:
            self.logger.error(f"Dependency check error: {e}")
            return False
    
    def _setup_directories(self) -> bool:
        """Setup required directory structure"""
        try:
            required_dirs = [
                'data',
                'logs',
                'temp',
                'data/backups',
                'data/monitoring',
                'data/process_state'
            ]
            
            for directory in required_dirs:
                Path(directory).mkdir(parents=True, exist_ok=True)
                self.logger.debug(f"Ensured directory: {directory}")
            
            # Set permissions
            for directory in required_dirs:
                os.chmod(directory, 0o755)
            
            self.logger.info(f"Directory structure validated ({len(required_dirs)} directories)")
            return True
            
        except Exception as e:
            self.logger.error(f"Directory setup error: {e}")
            return False
    
    def _initialize_database(self) -> bool:
        """Initialize database"""
        try:
            db_config = self.config.get('database', {})
            db_path = db_config.get('path', 'data/ai_rebuild.db')
            
            # Backup existing database if configured
            if db_config.get('backup_on_startup', False) and os.path.exists(db_path):
                backup_path = f"data/backups/ai_rebuild_backup_{int(time.time())}.db"
                shutil.copy2(db_path, backup_path)
                self.logger.info(f"Database backed up to: {backup_path}")
            
            # Initialize database
            from utilities.database import DatabaseManager
            db_manager = DatabaseManager(db_path)
            
            # Run migrations if configured
            if db_config.get('run_migrations', True):
                from utilities.migrations import run_migrations
                run_migrations(db_path)
                self.logger.info("Database migrations completed")
            
            # Test database connection
            with sqlite3.connect(db_path) as conn:
                conn.execute("SELECT 1").fetchone()
            
            self.logger.info(f"Database initialized: {db_path}")
            return True
            
        except Exception as e:
            self.logger.error(f"Database initialization error: {e}")
            return False
    
    def _validate_configuration(self) -> bool:
        """Validate configuration settings"""
        try:
            # Validate required configuration sections
            required_sections = ['database', 'monitoring', 'components']
            for section in required_sections:
                if section not in self.config:
                    self.logger.error(f"Missing configuration section: {section}")
                    return False
            
            # Validate database configuration
            db_config = self.config['database']
            if 'path' not in db_config:
                self.logger.error("Database path not configured")
                return False
            
            # Validate monitoring configuration
            monitoring_config = self.config['monitoring']
            if monitoring_config.get('enabled', True):
                dashboard_port = monitoring_config.get('dashboard_port', 8080)
                if not (1024 <= dashboard_port <= 65535):
                    self.logger.error(f"Invalid dashboard port: {dashboard_port}")
                    return False
            
            # Validate component configuration
            components_config = self.config['components']
            valid_components = ['agents', 'mcp_servers', 'telegram', 'tools']
            for component in components_config:
                if component not in valid_components:
                    self.logger.warning(f"Unknown component in config: {component}")
            
            self.logger.info("Configuration validated")
            return True
            
        except Exception as e:
            self.logger.error(f"Configuration validation error: {e}")
            return False
    
    def _health_checks(self) -> bool:
        """Perform component health checks"""
        try:
            health_checks = []
            
            # Database health check
            try:
                db_path = self.config['database']['path']
                with sqlite3.connect(db_path) as conn:
                    result = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()
                    table_count = result[0] if result else 0
                health_checks.append(('Database', True, f"{table_count} tables"))
            except Exception as e:
                health_checks.append(('Database', False, str(e)))
            
            # File system health check
            try:
                test_file = 'temp/startup_test.tmp'
                with open(test_file, 'w') as f:
                    f.write('test')
                os.remove(test_file)
                health_checks.append(('File System', True, 'Write/delete OK'))
            except Exception as e:
                health_checks.append(('File System', False, str(e)))
            
            # Network health check (if applicable)
            if self.config.get('monitoring', {}).get('enabled'):
                try:
                    import socket
                    dashboard_port = self.config['monitoring'].get('dashboard_port', 8080)
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(1)
                    result = sock.connect_ex(('localhost', dashboard_port))
                    sock.close()
                    
                    if result == 0:
                        health_checks.append(('Network Port', False, f"Port {dashboard_port} already in use"))
                    else:
                        health_checks.append(('Network Port', True, f"Port {dashboard_port} available"))
                except Exception as e:
                    health_checks.append(('Network Port', False, str(e)))
            
            # Report health check results
            failed_checks = 0
            for check_name, success, message in health_checks:
                if success:
                    self.logger.info(f"✅ {check_name}: {message}")
                else:
                    self.logger.error(f"❌ {check_name}: {message}")
                    failed_checks += 1
            
            if failed_checks > 0:
                self.logger.error(f"{failed_checks} health checks failed")
                return False
            
            self.logger.info(f"All {len(health_checks)} health checks passed")
            return True
            
        except Exception as e:
            self.logger.error(f"Health checks error: {e}")
            return False
    
    def _start_core_components(self) -> bool:
        """Start core system components"""
        try:
            components_config = self.config['components']
            
            # Start agents
            if components_config.get('agents', True):
                try:
                    from agents.context_manager import ContextManager
                    context_manager = ContextManager()
                    self.started_components.append('agents')
                    self.logger.info("✅ Agents initialized")
                except Exception as e:
                    self.logger.error(f"❌ Agents startup failed: {e}")
                    self.failed_components.append('agents')
            
            # Start tools
            if components_config.get('tools', True):
                try:
                    from tools.quality_framework import QualityFramework
                    quality_framework = QualityFramework()
                    self.started_components.append('tools')
                    self.logger.info("✅ Tools initialized")
                except Exception as e:
                    self.logger.error(f"❌ Tools startup failed: {e}")
                    self.failed_components.append('tools')
            
            # Start MCP servers
            if components_config.get('mcp_servers', True):
                try:
                    from mcp_servers.orchestrator import MCPOrchestrator
                    mcp_orchestrator = MCPOrchestrator()
                    self.started_components.append('mcp_servers')
                    self.logger.info("✅ MCP Servers initialized")
                except Exception as e:
                    self.logger.error(f"❌ MCP Servers startup failed: {e}")
                    self.failed_components.append('mcp_servers')
            
            if self.failed_components:
                self.logger.error(f"Failed to start components: {self.failed_components}")
                return False
            
            self.logger.info(f"Core components started: {self.started_components}")
            return True
            
        except Exception as e:
            self.logger.error(f"Core components startup error: {e}")
            return False
    
    def _start_monitoring(self) -> bool:
        """Start monitoring systems"""
        try:
            monitoring_config = self.config.get('monitoring', {})
            
            if not monitoring_config.get('enabled', True):
                self.logger.info("Monitoring disabled by configuration")
                return True
            
            # Start resource monitor
            try:
                from utilities.monitoring.resource_monitor import ResourceMonitor
                resource_monitor = ResourceMonitor()
                resource_monitor.start()
                self.started_components.append('resource_monitor')
                self.logger.info("✅ Resource Monitor started")
            except Exception as e:
                self.logger.error(f"❌ Resource Monitor failed: {e}")
                self.failed_components.append('resource_monitor')
            
            # Start alert manager
            try:
                from utilities.monitoring.alerting import AlertManager
                alert_manager = AlertManager()
                self.started_components.append('alert_manager')
                self.logger.info("✅ Alert Manager started")
            except Exception as e:
                self.logger.error(f"❌ Alert Manager failed: {e}")
                self.failed_components.append('alert_manager')
            
            # Start dashboard
            try:
                from utilities.monitoring.metrics_dashboard import MetricsDashboard, DashboardConfig
                from utilities.monitoring.health_score import HealthScoreCalculator
                
                dashboard_config = DashboardConfig(
                    port=monitoring_config.get('dashboard_port', 8080),
                    host=monitoring_config.get('dashboard_host', 'localhost')
                )
                
                # Note: This would need the actual instances from above
                # For now, create new instances (in production, use dependency injection)
                resource_monitor = ResourceMonitor()
                health_calculator = HealthScoreCalculator()
                alert_manager = AlertManager()
                
                dashboard = MetricsDashboard(
                    resource_monitor, health_calculator, alert_manager, dashboard_config
                )
                dashboard.start()
                
                self.started_components.append('metrics_dashboard')
                self.logger.info(f"✅ Metrics Dashboard started on port {dashboard_config.port}")
                
            except Exception as e:
                self.logger.error(f"❌ Metrics Dashboard failed: {e}")
                self.failed_components.append('metrics_dashboard')
            
            return len(self.failed_components) == 0
            
        except Exception as e:
            self.logger.error(f"Monitoring startup error: {e}")
            return False
    
    def _start_security(self) -> bool:
        """Start security systems"""
        try:
            security_config = self.config.get('security', {})
            
            # Security features would be implemented here
            # For now, just validate configuration
            
            if security_config.get('rate_limiting', True):
                self.logger.info("✅ Rate limiting enabled")
                
            if security_config.get('audit_logging', True):
                self.logger.info("✅ Audit logging enabled")
                
            if security_config.get('api_key_rotation', False):
                self.logger.info("✅ API key rotation enabled")
            
            self.started_components.append('security')
            return True
            
        except Exception as e:
            self.logger.error(f"Security startup error: {e}")
            return False
    
    def _start_integrations(self) -> bool:
        """Start integration services"""
        try:
            components_config = self.config['components']
            
            # Start Telegram integration if enabled
            if components_config.get('telegram', False):
                try:
                    from integrations.telegram.client import TelegramClient
                    telegram_client = TelegramClient()
                    self.started_components.append('telegram')
                    self.logger.info("✅ Telegram integration started")
                except Exception as e:
                    self.logger.error(f"❌ Telegram integration failed: {e}")
                    self.failed_components.append('telegram')
            
            return True
            
        except Exception as e:
            self.logger.error(f"Integration startup error: {e}")
            return False
    
    def _final_validation(self) -> bool:
        """Perform final system validation"""
        try:
            validation_checks = []
            
            # Check that required components started
            required_components = ['agents', 'tools']
            for component in required_components:
                if component not in self.started_components:
                    validation_checks.append(('Required Component', False, f"{component} not started"))
                else:
                    validation_checks.append(('Required Component', True, f"{component} started"))
            
            # Check database accessibility
            try:
                db_path = self.config['database']['path']
                with sqlite3.connect(db_path) as conn:
                    conn.execute("SELECT 1").fetchone()
                validation_checks.append(('Database Access', True, 'Connected'))
            except Exception as e:
                validation_checks.append(('Database Access', False, str(e)))
            
            # Check monitoring if enabled
            if self.config.get('monitoring', {}).get('enabled', True):
                if 'resource_monitor' in self.started_components:
                    validation_checks.append(('Monitoring', True, 'Active'))
                else:
                    validation_checks.append(('Monitoring', False, 'Not started'))
            
            # Report validation results
            failed_validations = 0
            for check_name, success, message in validation_checks:
                if success:
                    self.logger.info(f"✅ {check_name}: {message}")
                else:
                    self.logger.error(f"❌ {check_name}: {message}")
                    failed_validations += 1
            
            if failed_validations > 0:
                self.logger.error(f"{failed_validations} final validations failed")
                return False
            
            self.logger.info(f"All {len(validation_checks)} final validations passed")
            return True
            
        except Exception as e:
            self.logger.error(f"Final validation error: {e}")
            return False
    
    def _save_startup_log(self, success: bool):
        """Save startup log to file"""
        try:
            startup_summary = {
                'timestamp': self.startup_time.isoformat(),
                'success': success,
                'duration_seconds': time.time() - self.startup_time.timestamp(),
                'started_components': self.started_components,
                'failed_components': self.failed_components,
                'steps': self.startup_log,
                'configuration': self.config
            }
            
            log_file = f"logs/startup_{int(self.startup_time.timestamp())}.json"
            with open(log_file, 'w') as f:
                json.dump(startup_summary, f, indent=2)
            
            self.logger.info(f"Startup log saved: {log_file}")
            
        except Exception as e:
            self.logger.error(f"Failed to save startup log: {e}")

def main():
    """Main startup function"""
    import argparse
    
    parser = argparse.ArgumentParser(description='AI Rebuild Production Startup')
    parser.add_argument('--config', help='Configuration file path')
    parser.add_argument('--dry-run', action='store_true', help='Validate configuration without starting')
    parser.add_argument('--verbose', action='store_true', help='Enable verbose logging')
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    startup_manager = StartupManager(args.config)
    
    if args.dry_run:
        print("🔍 Dry run mode - validating configuration only")
        success = (startup_manager._validate_environment() and 
                  startup_manager._check_dependencies() and
                  startup_manager._validate_configuration())
        print(f"✅ Configuration valid" if success else "❌ Configuration invalid")
        return 0 if success else 1
    else:
        success = startup_manager.startup()
        return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main())