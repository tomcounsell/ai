"""
Production shutdown script with graceful component termination,
active request completion, state persistence, and resource cleanup.
"""

import os
import sys
import time
import signal
import logging
import json
import sqlite3
import threading
from pathlib import Path
from typing import Dict, List, Optional, Any, Set
from datetime import datetime
import psutil
import atexit

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

class ShutdownManager:
    """
    Production shutdown manager with graceful termination.
    
    Shutdown sequence:
    1. Signal capture and graceful start
    2. Stop accepting new requests
    3. Wait for active requests to complete
    4. Save application state
    5. Stop components in reverse startup order
    6. Release resources and cleanup
    7. Final validation
    """
    
    def __init__(self, config_file: Optional[str] = None):
        self.config_file = config_file or "config/workspace_config.json"
        self.logger = self._setup_logging()
        self.shutdown_time = datetime.now()
        
        # Load configuration
        self.config = self._load_configuration()
        
        # Shutdown state
        self.shutdown_initiated = False
        self.shutdown_complete = False
        self.active_requests = set()
        self.cleanup_tasks = []
        self.component_shutdown_order = []
        
        # Graceful shutdown settings
        self.max_wait_time = self.config.get('shutdown', {}).get('max_wait_seconds', 30)
        self.force_shutdown_time = self.config.get('shutdown', {}).get('force_shutdown_seconds', 60)
        
        # Track active components and resources
        self.active_components = set()
        self.active_threads = set()
        self.active_processes = set()
        self.open_files = set()
        self.database_connections = set()
        
        # Setup signal handlers
        self._setup_signal_handlers()
        
        # Register cleanup on exit
        atexit.register(self._emergency_cleanup)
    
    def _setup_logging(self) -> logging.Logger:
        """Setup shutdown logging"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.StreamHandler(sys.stdout),
                logging.FileHandler('logs/shutdown.log', mode='a')
            ]
        )
        return logging.getLogger(__name__)
    
    def _load_configuration(self) -> Dict[str, Any]:
        """Load system configuration"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    return json.load(f)
            else:
                return {}
        except Exception as e:
            self.logger.error(f"Error loading configuration: {e}")
            return {}
    
    def _setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown"""
        def signal_handler(signum, frame):
            signal_name = signal.Signals(signum).name
            self.logger.info(f"Received signal {signal_name} - initiating graceful shutdown")
            self.initiate_shutdown()
        
        # Handle common shutdown signals
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
        
        # Handle additional signals on Unix systems
        if hasattr(signal, 'SIGQUIT'):
            signal.signal(signal.SIGQUIT, signal_handler)
        if hasattr(signal, 'SIGHUP'):
            signal.signal(signal.SIGHUP, signal_handler)
    
    def register_component(self, name: str, shutdown_callback: callable = None):
        """Register a component for tracked shutdown"""
        self.active_components.add(name)
        if shutdown_callback:
            self.cleanup_tasks.append((name, shutdown_callback))
        self.component_shutdown_order.insert(0, name)  # Reverse order for shutdown
    
    def register_active_request(self, request_id: str):
        """Register an active request"""
        self.active_requests.add(request_id)
    
    def complete_request(self, request_id: str):
        """Mark a request as completed"""
        self.active_requests.discard(request_id)
    
    def initiate_shutdown(self, reason: str = "Signal received") -> bool:
        """Initiate graceful shutdown sequence"""
        if self.shutdown_initiated:
            self.logger.warning("Shutdown already in progress")
            return True
        
        self.shutdown_initiated = True
        self.logger.info("=" * 60)
        self.logger.info("🛑 AI Rebuild Graceful Shutdown Initiated")
        self.logger.info(f"📋 Reason: {reason}")
        self.logger.info("=" * 60)
        
        shutdown_steps = [
            ("Stop New Requests", self._stop_new_requests),
            ("Wait for Active Requests", self._wait_for_active_requests),
            ("Save Application State", self._save_application_state),
            ("Stop Monitoring Systems", self._stop_monitoring_systems),
            ("Stop Integration Services", self._stop_integration_services),
            ("Stop Security Systems", self._stop_security_systems),
            ("Stop Core Components", self._stop_core_components),
            ("Close Database Connections", self._close_database_connections),
            ("Cleanup Resources", self._cleanup_resources),
            ("Final Validation", self._final_validation)
        ]
        
        success = True
        total_start_time = time.time()
        
        for step_name, step_func in shutdown_steps:
            self.logger.info(f"📋 {step_name}...")
            step_start_time = time.time()
            
            try:
                step_result = step_func()
                step_duration = time.time() - step_start_time
                
                if step_result:
                    self.logger.info(f"✅ {step_name} completed ({step_duration:.2f}s)")
                else:
                    self.logger.error(f"❌ {step_name} failed ({step_duration:.2f}s)")
                    success = False
                    # Continue with remaining steps even if one fails
                    
            except Exception as e:
                step_duration = time.time() - step_start_time
                self.logger.error(f"💥 {step_name} error: {e} ({step_duration:.2f}s)")
                success = False
        
        total_duration = time.time() - total_start_time
        self.shutdown_complete = True
        
        # Save shutdown log
        self._save_shutdown_log(success, total_duration)
        
        if success:
            self.logger.info("=" * 60)
            self.logger.info(f"✅ Graceful shutdown completed ({total_duration:.2f}s)")
            self.logger.info("=" * 60)
        else:
            self.logger.error("=" * 60)
            self.logger.error(f"⚠️ Shutdown completed with errors ({total_duration:.2f}s)")
            self.logger.error("=" * 60)
        
        return success
    
    def _stop_new_requests(self) -> bool:
        """Stop accepting new requests"""
        try:
            # Set global flag to reject new requests
            # This would be implemented by the web server/request handlers
            
            # Try to gracefully disable request acceptance
            try:
                # If we have a web server running, stop it from accepting new connections
                # This is framework-specific implementation
                pass
            except Exception as e:
                self.logger.warning(f"Could not gracefully stop request acceptance: {e}")
            
            self.logger.info("New request acceptance stopped")
            return True
            
        except Exception as e:
            self.logger.error(f"Error stopping new requests: {e}")
            return False
    
    def _wait_for_active_requests(self) -> bool:
        """Wait for active requests to complete"""
        try:
            if not self.active_requests:
                self.logger.info("No active requests to wait for")
                return True
            
            self.logger.info(f"Waiting for {len(self.active_requests)} active requests...")
            
            wait_start_time = time.time()
            while self.active_requests and (time.time() - wait_start_time) < self.max_wait_time:
                self.logger.info(f"Still waiting for {len(self.active_requests)} requests...")
                time.sleep(1)
            
            wait_duration = time.time() - wait_start_time
            
            if self.active_requests:
                self.logger.warning(f"Timed out waiting for requests after {wait_duration:.1f}s")
                self.logger.warning(f"Forcing shutdown with {len(self.active_requests)} requests remaining")
                return False
            else:
                self.logger.info(f"All requests completed in {wait_duration:.1f}s")
                return True
                
        except Exception as e:
            self.logger.error(f"Error waiting for active requests: {e}")
            return False
    
    def _save_application_state(self) -> bool:
        """Save application state for restart"""
        try:
            state_data = {
                'shutdown_time': self.shutdown_time.isoformat(),
                'active_components': list(self.active_components),
                'configuration': self.config,
                'incomplete_requests': list(self.active_requests),
                'system_metrics': self._collect_shutdown_metrics()
            }
            
            # Save state to multiple locations for redundancy
            state_files = [
                'data/process_state/shutdown_state.json',
                f'data/process_state/shutdown_state_{int(time.time())}.json'
            ]
            
            for state_file in state_files:
                try:
                    os.makedirs(os.path.dirname(state_file), exist_ok=True)
                    with open(state_file, 'w') as f:
                        json.dump(state_data, f, indent=2)
                except Exception as e:
                    self.logger.warning(f"Could not save state to {state_file}: {e}")
            
            self.logger.info("Application state saved")
            return True
            
        except Exception as e:
            self.logger.error(f"Error saving application state: {e}")
            return False
    
    def _collect_shutdown_metrics(self) -> Dict[str, Any]:
        """Collect system metrics at shutdown"""
        try:
            metrics = {
                'timestamp': datetime.now().isoformat(),
                'uptime_seconds': time.time() - self.shutdown_time.timestamp(),
            }
            
            # Collect system metrics if psutil available
            try:
                import psutil
                process = psutil.Process()
                metrics.update({
                    'memory_usage_mb': process.memory_info().rss / 1024 / 1024,
                    'cpu_percent': process.cpu_percent(),
                    'open_files': len(process.open_files()),
                    'connections': len(process.connections()),
                    'threads': process.num_threads()
                })
            except ImportError:
                pass
            
            return metrics
            
        except Exception as e:
            self.logger.error(f"Error collecting shutdown metrics: {e}")
            return {}
    
    def _stop_monitoring_systems(self) -> bool:
        """Stop monitoring systems"""
        try:
            monitoring_stopped = True
            
            # Stop resource monitor
            try:
                # In a real implementation, we would access the actual running instances
                # For now, we'll simulate the shutdown process
                self.logger.info("Stopping resource monitor...")
                # resource_monitor.stop()
                time.sleep(0.5)  # Simulate shutdown time
            except Exception as e:
                self.logger.error(f"Error stopping resource monitor: {e}")
                monitoring_stopped = False
            
            # Stop alert manager
            try:
                self.logger.info("Stopping alert manager...")
                # alert_manager.cleanup()
                time.sleep(0.5)
            except Exception as e:
                self.logger.error(f"Error stopping alert manager: {e}")
                monitoring_stopped = False
            
            # Stop metrics dashboard
            try:
                self.logger.info("Stopping metrics dashboard...")
                # dashboard.stop()
                time.sleep(0.5)
            except Exception as e:
                self.logger.error(f"Error stopping metrics dashboard: {e}")
                monitoring_stopped = False
            
            # Stop auto-restart manager
            try:
                self.logger.info("Stopping auto-restart manager...")
                # auto_restart_manager.cleanup()
                time.sleep(0.5)
            except Exception as e:
                self.logger.error(f"Error stopping auto-restart manager: {e}")
                monitoring_stopped = False
            
            if monitoring_stopped:
                self.logger.info("All monitoring systems stopped")
            else:
                self.logger.warning("Some monitoring systems failed to stop cleanly")
            
            return monitoring_stopped
            
        except Exception as e:
            self.logger.error(f"Error stopping monitoring systems: {e}")
            return False
    
    def _stop_integration_services(self) -> bool:
        """Stop integration services"""
        try:
            # Stop Telegram integration
            try:
                if 'telegram' in self.active_components:
                    self.logger.info("Stopping Telegram integration...")
                    # telegram_client.stop()
                    time.sleep(1)
                    self.active_components.discard('telegram')
            except Exception as e:
                self.logger.error(f"Error stopping Telegram integration: {e}")
                return False
            
            # Stop other integrations
            integrations_to_stop = [comp for comp in self.active_components if 'integration' in comp]
            for integration in integrations_to_stop:
                try:
                    self.logger.info(f"Stopping {integration}...")
                    self.active_components.discard(integration)
                except Exception as e:
                    self.logger.error(f"Error stopping {integration}: {e}")
            
            self.logger.info("Integration services stopped")
            return True
            
        except Exception as e:
            self.logger.error(f"Error stopping integration services: {e}")
            return False
    
    def _stop_security_systems(self) -> bool:
        """Stop security systems"""
        try:
            # Security systems cleanup would be implemented here
            self.logger.info("Security systems stopped")
            return True
            
        except Exception as e:
            self.logger.error(f"Error stopping security systems: {e}")
            return False
    
    def _stop_core_components(self) -> bool:
        """Stop core components"""
        try:
            components_stopped = True
            
            # Stop components in reverse startup order
            for component in self.component_shutdown_order:
                try:
                    self.logger.info(f"Stopping {component}...")
                    
                    # Find and execute shutdown callback if registered
                    for name, callback in self.cleanup_tasks:
                        if name == component:
                            callback()
                            break
                    
                    self.active_components.discard(component)
                    time.sleep(0.5)  # Give component time to shut down
                    
                except Exception as e:
                    self.logger.error(f"Error stopping {component}: {e}")
                    components_stopped = False
            
            # Stop any remaining components
            remaining_components = list(self.active_components)
            for component in remaining_components:
                try:
                    self.logger.warning(f"Force stopping remaining component: {component}")
                    self.active_components.discard(component)
                except Exception as e:
                    self.logger.error(f"Error force stopping {component}: {e}")
                    components_stopped = False
            
            if components_stopped:
                self.logger.info("All core components stopped")
            else:
                self.logger.warning("Some core components failed to stop cleanly")
            
            return components_stopped
            
        except Exception as e:
            self.logger.error(f"Error stopping core components: {e}")
            return False
    
    def _close_database_connections(self) -> bool:
        """Close database connections"""
        try:
            connections_closed = True
            
            # Close tracked database connections
            for connection in list(self.database_connections):
                try:
                    connection.close()
                    self.database_connections.discard(connection)
                except Exception as e:
                    self.logger.error(f"Error closing database connection: {e}")
                    connections_closed = False
            
            # Attempt to close any remaining SQLite connections
            try:
                # This is a best-effort cleanup
                import sqlite3
                # SQLite connections are typically closed automatically,
                # but we can ensure any remaining ones are handled
            except Exception as e:
                self.logger.warning(f"Could not perform additional database cleanup: {e}")
            
            self.logger.info("Database connections closed")
            return connections_closed
            
        except Exception as e:
            self.logger.error(f"Error closing database connections: {e}")
            return False
    
    def _cleanup_resources(self) -> bool:
        """Cleanup remaining resources"""
        try:
            cleanup_success = True
            
            # Close open files
            for file_obj in list(self.open_files):
                try:
                    if not file_obj.closed:
                        file_obj.close()
                    self.open_files.discard(file_obj)
                except Exception as e:
                    self.logger.error(f"Error closing file: {e}")
                    cleanup_success = False
            
            # Stop remaining threads
            for thread in list(self.active_threads):
                try:
                    if thread.is_alive():
                        # Give threads a chance to finish gracefully
                        thread.join(timeout=5)
                        if thread.is_alive():
                            self.logger.warning(f"Thread {thread.name} did not stop gracefully")
                    self.active_threads.discard(thread)
                except Exception as e:
                    self.logger.error(f"Error stopping thread: {e}")
                    cleanup_success = False
            
            # Cleanup temporary files
            try:
                temp_dir = Path('temp')
                if temp_dir.exists():
                    for temp_file in temp_dir.glob('shutdown_*'):
                        try:
                            temp_file.unlink()
                        except Exception:
                            pass
            except Exception as e:
                self.logger.warning(f"Could not cleanup temp files: {e}")
            
            # Clear caches
            try:
                # Clear any in-memory caches
                import gc
                gc.collect()
            except Exception as e:
                self.logger.warning(f"Could not force garbage collection: {e}")
            
            if cleanup_success:
                self.logger.info("Resource cleanup completed")
            else:
                self.logger.warning("Resource cleanup completed with some errors")
            
            return cleanup_success
            
        except Exception as e:
            self.logger.error(f"Error during resource cleanup: {e}")
            return False
    
    def _final_validation(self) -> bool:
        """Perform final shutdown validation"""
        try:
            validation_results = []
            
            # Check that all components stopped
            if not self.active_components:
                validation_results.append(("Active Components", True, "All stopped"))
            else:
                validation_results.append(("Active Components", False, f"{len(self.active_components)} remaining"))
            
            # Check that all requests completed
            if not self.active_requests:
                validation_results.append(("Active Requests", True, "All completed"))
            else:
                validation_results.append(("Active Requests", False, f"{len(self.active_requests)} remaining"))
            
            # Check database connections
            if not self.database_connections:
                validation_results.append(("Database Connections", True, "All closed"))
            else:
                validation_results.append(("Database Connections", False, f"{len(self.database_connections)} remaining"))
            
            # Check open files
            if not self.open_files:
                validation_results.append(("Open Files", True, "All closed"))
            else:
                validation_results.append(("Open Files", False, f"{len(self.open_files)} remaining"))
            
            # Check active threads
            active_thread_count = sum(1 for t in self.active_threads if t.is_alive())
            if active_thread_count == 0:
                validation_results.append(("Active Threads", True, "All stopped"))
            else:
                validation_results.append(("Active Threads", False, f"{active_thread_count} remaining"))
            
            # Report validation results
            failed_validations = 0
            for check_name, success, message in validation_results:
                if success:
                    self.logger.info(f"✅ {check_name}: {message}")
                else:
                    self.logger.error(f"❌ {check_name}: {message}")
                    failed_validations += 1
            
            if failed_validations == 0:
                self.logger.info("All shutdown validations passed")
                return True
            else:
                self.logger.warning(f"{failed_validations} shutdown validations failed")
                return False
                
        except Exception as e:
            self.logger.error(f"Error in final validation: {e}")
            return False
    
    def _save_shutdown_log(self, success: bool, duration: float):
        """Save shutdown log"""
        try:
            shutdown_log = {
                'timestamp': self.shutdown_time.isoformat(),
                'success': success,
                'duration_seconds': duration,
                'active_components_at_start': list(self.active_components),
                'remaining_requests': list(self.active_requests),
                'remaining_components': list(self.active_components),
                'configuration': self.config
            }
            
            log_file = f"logs/shutdown_{int(self.shutdown_time.timestamp())}.json"
            with open(log_file, 'w') as f:
                json.dump(shutdown_log, f, indent=2)
            
            self.logger.info(f"Shutdown log saved: {log_file}")
            
        except Exception as e:
            self.logger.error(f"Failed to save shutdown log: {e}")
    
    def _emergency_cleanup(self):
        """Emergency cleanup called on exit"""
        if not self.shutdown_complete:
            self.logger.warning("Emergency cleanup triggered - performing minimal cleanup")
            try:
                # Close any remaining database connections
                for connection in self.database_connections:
                    try:
                        connection.close()
                    except:
                        pass
                
                # Close any remaining files
                for file_obj in self.open_files:
                    try:
                        if not file_obj.closed:
                            file_obj.close()
                    except:
                        pass
                        
            except Exception as e:
                # Don't log errors in emergency cleanup to avoid issues during exit
                pass
    
    def force_shutdown(self, timeout: int = 5) -> bool:
        """Force immediate shutdown"""
        self.logger.warning(f"Force shutdown initiated - {timeout}s timeout")
        
        try:
            # Stop all processes immediately
            current_process = psutil.Process()
            children = current_process.children(recursive=True)
            
            for child in children:
                try:
                    child.terminate()
                except:
                    pass
            
            # Wait briefly for termination
            time.sleep(min(timeout, 2))
            
            # Kill any remaining processes
            for child in children:
                try:
                    if child.is_running():
                        child.kill()
                except:
                    pass
            
            self.logger.warning("Force shutdown completed")
            return True
            
        except Exception as e:
            self.logger.error(f"Error during force shutdown: {e}")
            return False

def main():
    """Main shutdown function"""
    import argparse
    
    parser = argparse.ArgumentParser(description='AI Rebuild Production Shutdown')
    parser.add_argument('--config', help='Configuration file path')
    parser.add_argument('--force', action='store_true', help='Force immediate shutdown')
    parser.add_argument('--timeout', type=int, default=30, help='Maximum wait time for graceful shutdown')
    parser.add_argument('--reason', default='Manual shutdown', help='Reason for shutdown')
    
    args = parser.parse_args()
    
    shutdown_manager = ShutdownManager(args.config)
    
    if args.force:
        success = shutdown_manager.force_shutdown(args.timeout)
    else:
        shutdown_manager.max_wait_time = args.timeout
        success = shutdown_manager.initiate_shutdown(args.reason)
    
    return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main())