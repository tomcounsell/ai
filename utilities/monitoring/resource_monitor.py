"""
Comprehensive resource monitoring for production systems.
Monitors CPU, memory, disk, network, and database resources.
"""

import psutil
import time
import threading
import sqlite3
import logging
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass
from datetime import datetime, timedelta
import asyncio
import json
import os

@dataclass
class ResourceMetrics:
    """Container for system resource metrics"""
    timestamp: datetime
    cpu_percent: float
    memory_percent: float
    disk_percent: float
    disk_io_read: int
    disk_io_write: int
    network_bytes_sent: int
    network_bytes_recv: int
    process_count: int
    load_average: Optional[List[float]]
    database_connections: Optional[int] = None
    database_query_time: Optional[float] = None

@dataclass
class AlertThreshold:
    """Alert threshold configuration"""
    metric: str
    warning_threshold: float
    critical_threshold: float
    duration_seconds: int = 300  # 5 minutes

class ResourceMonitor:
    """
    Production-grade resource monitoring system.
    
    Features:
    - Real-time system metrics collection
    - Database performance monitoring
    - Configurable alert thresholds
    - Historical data storage
    - Performance trend analysis
    """
    
    def __init__(self, 
                 db_path: str = "data/monitoring.db",
                 collection_interval: int = 30,
                 retention_days: int = 30):
        self.db_path = db_path
        self.collection_interval = collection_interval
        self.retention_days = retention_days
        self.logger = logging.getLogger(__name__)
        
        # Monitoring state
        self.running = False
        self.monitor_thread = None
        self._callbacks: List[Callable[[ResourceMetrics], None]] = []
        
        # Alert thresholds
        self.thresholds = {
            'cpu_percent': AlertThreshold('cpu_percent', 70.0, 90.0),
            'memory_percent': AlertThreshold('memory_percent', 80.0, 95.0),
            'disk_percent': AlertThreshold('disk_percent', 85.0, 95.0),
            'database_query_time': AlertThreshold('database_query_time', 1.0, 5.0)
        }
        
        # Alert state tracking
        self._alert_states = {}
        
        # Initialize database
        self._init_database()
    
    def _init_database(self):
        """Initialize monitoring database"""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS resource_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME NOT NULL,
                    cpu_percent REAL NOT NULL,
                    memory_percent REAL NOT NULL,
                    disk_percent REAL NOT NULL,
                    disk_io_read INTEGER NOT NULL,
                    disk_io_write INTEGER NOT NULL,
                    network_bytes_sent INTEGER NOT NULL,
                    network_bytes_recv INTEGER NOT NULL,
                    process_count INTEGER NOT NULL,
                    load_average TEXT,
                    database_connections INTEGER,
                    database_query_time REAL
                )
            ''')
            
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_timestamp 
                ON resource_metrics(timestamp)
            ''')
            
            conn.execute('''
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME NOT NULL,
                    metric TEXT NOT NULL,
                    level TEXT NOT NULL,
                    value REAL NOT NULL,
                    threshold REAL NOT NULL,
                    message TEXT NOT NULL
                )
            ''')
    
    def add_callback(self, callback: Callable[[ResourceMetrics], None]):
        """Add callback for metric updates"""
        self._callbacks.append(callback)
    
    def remove_callback(self, callback: Callable[[ResourceMetrics], None]):
        """Remove metric update callback"""
        if callback in self._callbacks:
            self._callbacks.remove(callback)
    
    def set_threshold(self, metric: str, warning: float, critical: float, duration: int = 300):
        """Set alert threshold for a metric"""
        self.thresholds[metric] = AlertThreshold(metric, warning, critical, duration)
    
    def collect_metrics(self, include_db: bool = True) -> ResourceMetrics:
        """Collect current system metrics"""
        try:
            # CPU metrics
            cpu_percent = psutil.cpu_percent(interval=1)
            
            # Memory metrics
            memory = psutil.virtual_memory()
            memory_percent = memory.percent
            
            # Disk metrics
            disk = psutil.disk_usage('/')
            disk_percent = disk.percent
            
            # Disk I/O
            disk_io = psutil.disk_io_counters()
            disk_io_read = disk_io.read_bytes if disk_io else 0
            disk_io_write = disk_io.write_bytes if disk_io else 0
            
            # Network I/O
            net_io = psutil.net_io_counters()
            network_bytes_sent = net_io.bytes_sent if net_io else 0
            network_bytes_recv = net_io.bytes_recv if net_io else 0
            
            # Process count
            process_count = len(psutil.pids())
            
            # Load average (Unix systems)
            load_average = None
            try:
                load_average = list(os.getloadavg())
            except (OSError, AttributeError):
                pass  # Windows doesn't have getloadavg
            
            # Database metrics
            database_connections = None
            database_query_time = None
            
            if include_db:
                database_connections, database_query_time = self._collect_db_metrics()
            
            metrics = ResourceMetrics(
                timestamp=datetime.now(),
                cpu_percent=cpu_percent,
                memory_percent=memory_percent,
                disk_percent=disk_percent,
                disk_io_read=disk_io_read,
                disk_io_write=disk_io_write,
                network_bytes_sent=network_bytes_sent,
                network_bytes_recv=network_bytes_recv,
                process_count=process_count,
                load_average=load_average,
                database_connections=database_connections,
                database_query_time=database_query_time
            )
            
            return metrics
            
        except Exception as e:
            self.logger.error(f"Error collecting metrics: {e}")
            raise
    
    def _collect_db_metrics(self) -> tuple[Optional[int], Optional[float]]:
        """Collect database-specific metrics"""
        try:
            start_time = time.time()
            
            with sqlite3.connect(self.db_path) as conn:
                # Simple query to measure response time
                conn.execute("SELECT 1").fetchone()
                query_time = time.time() - start_time
                
                # Count active connections (simplified for SQLite)
                connections = 1  # SQLite doesn't have connection pooling like PostgreSQL
                
            return connections, query_time
            
        except Exception as e:
            self.logger.warning(f"Error collecting database metrics: {e}")
            return None, None
    
    def store_metrics(self, metrics: ResourceMetrics):
        """Store metrics in database"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('''
                    INSERT INTO resource_metrics (
                        timestamp, cpu_percent, memory_percent, disk_percent,
                        disk_io_read, disk_io_write, network_bytes_sent, 
                        network_bytes_recv, process_count, load_average,
                        database_connections, database_query_time
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    metrics.timestamp,
                    metrics.cpu_percent,
                    metrics.memory_percent,
                    metrics.disk_percent,
                    metrics.disk_io_read,
                    metrics.disk_io_write,
                    metrics.network_bytes_sent,
                    metrics.network_bytes_recv,
                    metrics.process_count,
                    json.dumps(metrics.load_average) if metrics.load_average else None,
                    metrics.database_connections,
                    metrics.database_query_time
                ))
                
        except Exception as e:
            self.logger.error(f"Error storing metrics: {e}")
    
    def check_alerts(self, metrics: ResourceMetrics):
        """Check metrics against alert thresholds"""
        alerts = []
        
        for metric_name, threshold in self.thresholds.items():
            value = getattr(metrics, metric_name, None)
            if value is None:
                continue
            
            alert_level = None
            if value >= threshold.critical_threshold:
                alert_level = 'critical'
            elif value >= threshold.warning_threshold:
                alert_level = 'warning'
            
            if alert_level:
                alert_key = f"{metric_name}_{alert_level}"
                current_time = datetime.now()
                
                # Track alert state
                if alert_key not in self._alert_states:
                    self._alert_states[alert_key] = current_time
                
                # Only fire alert if threshold duration exceeded
                if (current_time - self._alert_states[alert_key]).seconds >= threshold.duration_seconds:
                    alert = {
                        'timestamp': current_time,
                        'metric': metric_name,
                        'level': alert_level,
                        'value': value,
                        'threshold': threshold.critical_threshold if alert_level == 'critical' else threshold.warning_threshold,
                        'message': f"{metric_name.replace('_', ' ').title()} {alert_level}: {value:.2f}% (threshold: {threshold.critical_threshold if alert_level == 'critical' else threshold.warning_threshold}%)"
                    }
                    alerts.append(alert)
                    self._store_alert(alert)
            else:
                # Clear alert states for this metric
                for key in list(self._alert_states.keys()):
                    if key.startswith(f"{metric_name}_"):
                        del self._alert_states[key]
        
        return alerts
    
    def _store_alert(self, alert: Dict[str, Any]):
        """Store alert in database"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('''
                    INSERT INTO alerts (timestamp, metric, level, value, threshold, message)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    alert['timestamp'],
                    alert['metric'],
                    alert['level'],
                    alert['value'],
                    alert['threshold'],
                    alert['message']
                ))
        except Exception as e:
            self.logger.error(f"Error storing alert: {e}")
    
    def get_historical_metrics(self, 
                             hours: int = 24, 
                             metric: Optional[str] = None) -> List[Dict]:
        """Get historical metrics from database"""
        start_time = datetime.now() - timedelta(hours=hours)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            if metric:
                query = f'''
                    SELECT timestamp, {metric}
                    FROM resource_metrics 
                    WHERE timestamp >= ?
                    ORDER BY timestamp
                '''
            else:
                query = '''
                    SELECT * FROM resource_metrics 
                    WHERE timestamp >= ?
                    ORDER BY timestamp
                '''
            
            results = conn.execute(query, (start_time,)).fetchall()
            return [dict(row) for row in results]
    
    def get_recent_alerts(self, hours: int = 24) -> List[Dict]:
        """Get recent alerts from database"""
        start_time = datetime.now() - timedelta(hours=hours)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            results = conn.execute('''
                SELECT * FROM alerts 
                WHERE timestamp >= ?
                ORDER BY timestamp DESC
            ''', (start_time,)).fetchall()
            
            return [dict(row) for row in results]
    
    def cleanup_old_data(self):
        """Remove old monitoring data beyond retention period"""
        cutoff_date = datetime.now() - timedelta(days=self.retention_days)
        
        with sqlite3.connect(self.db_path) as conn:
            # Clean up old metrics
            conn.execute('''
                DELETE FROM resource_metrics 
                WHERE timestamp < ?
            ''', (cutoff_date,))
            
            # Clean up old alerts
            conn.execute('''
                DELETE FROM alerts 
                WHERE timestamp < ?
            ''', (cutoff_date,))
            
            conn.commit()
    
    def _monitoring_loop(self):
        """Main monitoring loop"""
        self.logger.info("Resource monitoring started")
        
        while self.running:
            try:
                # Collect metrics
                metrics = self.collect_metrics()
                
                # Store metrics
                self.store_metrics(metrics)
                
                # Check for alerts
                alerts = self.check_alerts(metrics)
                
                # Notify callbacks
                for callback in self._callbacks:
                    try:
                        callback(metrics)
                    except Exception as e:
                        self.logger.error(f"Error in callback: {e}")
                
                # Log alerts
                for alert in alerts:
                    if alert['level'] == 'critical':
                        self.logger.critical(alert['message'])
                    else:
                        self.logger.warning(alert['message'])
                
                # Periodic cleanup
                if int(time.time()) % 3600 == 0:  # Every hour
                    self.cleanup_old_data()
                
                time.sleep(self.collection_interval)
                
            except Exception as e:
                self.logger.error(f"Error in monitoring loop: {e}")
                time.sleep(self.collection_interval)
        
        self.logger.info("Resource monitoring stopped")
    
    def start(self):
        """Start resource monitoring"""
        if self.running:
            return
        
        self.running = True
        self.monitor_thread = threading.Thread(target=self._monitoring_loop, daemon=True)
        self.monitor_thread.start()
        self.logger.info("Resource monitor started")
    
    def stop(self):
        """Stop resource monitoring"""
        if not self.running:
            return
        
        self.running = False
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=10)
        
        self.logger.info("Resource monitor stopped")
    
    def get_current_status(self) -> Dict[str, Any]:
        """Get current system status summary"""
        try:
            metrics = self.collect_metrics()
            recent_alerts = self.get_recent_alerts(hours=1)
            
            return {
                'timestamp': metrics.timestamp.isoformat(),
                'status': 'healthy' if not recent_alerts else 'degraded',
                'metrics': {
                    'cpu_percent': metrics.cpu_percent,
                    'memory_percent': metrics.memory_percent,
                    'disk_percent': metrics.disk_percent,
                    'process_count': metrics.process_count,
                    'database_query_time': metrics.database_query_time
                },
                'recent_alerts': len(recent_alerts),
                'monitoring_enabled': self.running
            }
        except Exception as e:
            self.logger.error(f"Error getting current status: {e}")
            return {
                'status': 'error',
                'error': str(e),
                'monitoring_enabled': self.running
            }