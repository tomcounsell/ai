"""
Real-time metrics dashboard for production monitoring.
Provides web-based interface for system metrics and alerts.
"""

import json
import asyncio
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
import threading
import time
import sqlite3
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.parse as urlparse

from .resource_monitor import ResourceMonitor, ResourceMetrics
from .health_score import HealthScoreCalculator, HealthScore
from .alerting import AlertManager, AlertLevel, AlertStatus

@dataclass
class DashboardConfig:
    """Dashboard configuration"""
    port: int = 8080
    host: str = 'localhost'
    refresh_interval: int = 5  # seconds
    max_data_points: int = 100
    enable_auth: bool = False
    auth_token: Optional[str] = None

class MetricsDashboard:
    """
    Web-based metrics dashboard for production monitoring.
    
    Features:
    - Real-time metric visualization
    - Alert status display
    - Health score monitoring  
    - Historical trend charts
    - System status overview
    - Interactive controls
    """
    
    def __init__(self, 
                 resource_monitor: ResourceMonitor,
                 health_calculator: HealthScoreCalculator,
                 alert_manager: AlertManager,
                 config: DashboardConfig = None):
        
        self.resource_monitor = resource_monitor
        self.health_calculator = health_calculator
        self.alert_manager = alert_manager
        self.config = config or DashboardConfig()
        self.logger = logging.getLogger(__name__)
        
        # Dashboard state
        self.running = False
        self.server = None
        self.server_thread = None
        
        # Metrics cache
        self._metrics_cache: List[Dict] = []
        self._cache_lock = threading.RLock()
        
        # Start background data collection
        self._start_data_collector()
    
    def _start_data_collector(self):
        """Start background thread to collect metrics"""
        def collect_data():
            while True:
                try:
                    # Get current metrics
                    current_metrics = self.resource_monitor.collect_metrics()
                    current_health = self.health_calculator.calculate_health_score()
                    active_alerts = self.alert_manager.get_active_alerts()
                    
                    # Create dashboard data point
                    data_point = {
                        'timestamp': current_metrics.timestamp.isoformat(),
                        'metrics': {
                            'cpu_percent': current_metrics.cpu_percent,
                            'memory_percent': current_metrics.memory_percent,
                            'disk_percent': current_metrics.disk_percent,
                            'network_bytes_sent': current_metrics.network_bytes_sent,
                            'network_bytes_recv': current_metrics.network_bytes_recv,
                            'process_count': current_metrics.process_count,
                            'database_query_time': current_metrics.database_query_time,
                            'database_connections': current_metrics.database_connections
                        },
                        'health': {
                            'overall_score': current_health.overall_score,
                            'status': current_health.status,
                            'component_scores': current_health.component_scores
                        },
                        'alerts': {
                            'total': len(active_alerts),
                            'critical': len([a for a in active_alerts if a.level == AlertLevel.CRITICAL]),
                            'warning': len([a for a in active_alerts if a.level == AlertLevel.WARNING]),
                            'emergency': len([a for a in active_alerts if a.level == AlertLevel.EMERGENCY])
                        }
                    }
                    
                    # Add to cache
                    with self._cache_lock:
                        self._metrics_cache.append(data_point)
                        # Keep only recent data points
                        if len(self._metrics_cache) > self.config.max_data_points:
                            self._metrics_cache.pop(0)
                    
                    time.sleep(self.config.refresh_interval)
                    
                except Exception as e:
                    self.logger.error(f"Error collecting dashboard data: {e}")
                    time.sleep(self.config.refresh_interval)
        
        collector_thread = threading.Thread(target=collect_data, daemon=True)
        collector_thread.start()
    
    def start(self):
        """Start the dashboard web server"""
        if self.running:
            return
        
        try:
            self.server = HTTPServer(
                (self.config.host, self.config.port),
                lambda *args: DashboardRequestHandler(self, *args)
            )
            
            self.server_thread = threading.Thread(
                target=self.server.serve_forever,
                daemon=True
            )
            self.server_thread.start()
            self.running = True
            
            self.logger.info(f"Dashboard started at http://{self.config.host}:{self.config.port}")
            
        except Exception as e:
            self.logger.error(f"Failed to start dashboard: {e}")
            raise
    
    def stop(self):
        """Stop the dashboard web server"""
        if not self.running:
            return
        
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        
        if self.server_thread and self.server_thread.is_alive():
            self.server_thread.join(timeout=5)
        
        self.running = False
        self.logger.info("Dashboard stopped")
    
    def get_current_data(self) -> Dict[str, Any]:
        """Get current dashboard data"""
        with self._cache_lock:
            if not self._metrics_cache:
                return {'error': 'No data available'}
            
            latest_data = self._metrics_cache[-1].copy()
            
            # Add additional context
            latest_data['system_status'] = self._get_system_status()
            latest_data['recent_alerts'] = self._get_recent_alerts()
            latest_data['health_recommendations'] = self._get_health_recommendations()
            
            return latest_data
    
    def get_historical_data(self, hours: int = 24) -> List[Dict]:
        """Get historical data for charts"""
        # Get from database for longer history
        historical_metrics = self.resource_monitor.get_historical_metrics(hours)
        
        dashboard_data = []
        for metric_row in historical_metrics:
            data_point = {
                'timestamp': metric_row['timestamp'],
                'metrics': {
                    'cpu_percent': metric_row['cpu_percent'],
                    'memory_percent': metric_row['memory_percent'],
                    'disk_percent': metric_row['disk_percent'],
                    'database_query_time': metric_row['database_query_time']
                }
            }
            dashboard_data.append(data_point)
        
        return dashboard_data
    
    def get_metrics_series(self) -> Dict[str, List]:
        """Get metrics time series data for charting"""
        with self._cache_lock:
            if not self._metrics_cache:
                return {}
            
            series = {
                'timestamps': [],
                'cpu_percent': [],
                'memory_percent': [],
                'disk_percent': [],
                'health_score': [],
                'database_query_time': [],
                'alert_count': []
            }
            
            for data_point in self._metrics_cache:
                series['timestamps'].append(data_point['timestamp'])
                series['cpu_percent'].append(data_point['metrics']['cpu_percent'])
                series['memory_percent'].append(data_point['metrics']['memory_percent'])
                series['disk_percent'].append(data_point['metrics']['disk_percent'])
                series['health_score'].append(data_point['health']['overall_score'])
                
                db_time = data_point['metrics']['database_query_time']
                series['database_query_time'].append(db_time if db_time is not None else 0)
                
                series['alert_count'].append(data_point['alerts']['total'])
            
            return series
    
    def _get_system_status(self) -> Dict[str, Any]:
        """Get overall system status summary"""
        try:
            current_health = self.health_calculator.calculate_health_score()
            active_alerts = self.alert_manager.get_active_alerts()
            
            # Determine overall status
            if current_health.overall_score >= 97:
                overall_status = 'excellent'
                status_color = '#28a745'  # green
            elif current_health.overall_score >= 90:
                overall_status = 'good' 
                status_color = '#17a2b8'  # blue
            elif current_health.overall_score >= 75:
                overall_status = 'degraded'
                status_color = '#ffc107'  # yellow
            else:
                overall_status = 'critical'
                status_color = '#dc3545'  # red
            
            # Check for emergency alerts
            emergency_alerts = [a for a in active_alerts if a.level == AlertLevel.EMERGENCY]
            if emergency_alerts:
                overall_status = 'emergency'
                status_color = '#dc3545'
            
            return {
                'status': overall_status,
                'color': status_color,
                'health_score': current_health.overall_score,
                'active_alerts': len(active_alerts),
                'uptime': self._calculate_uptime(),
                'monitoring_enabled': self.resource_monitor.running
            }
            
        except Exception as e:
            self.logger.error(f"Error getting system status: {e}")
            return {
                'status': 'error',
                'color': '#6c757d',
                'error': str(e)
            }
    
    def _get_recent_alerts(self, limit: int = 10) -> List[Dict]:
        """Get recent alerts for display"""
        try:
            active_alerts = self.alert_manager.get_active_alerts()
            alert_history = self.alert_manager.get_alert_history(hours=24)
            
            # Combine and sort
            all_alerts = []
            
            # Add active alerts
            for alert in active_alerts:
                all_alerts.append({
                    'id': alert.id,
                    'title': alert.title,
                    'level': alert.level.value,
                    'status': alert.status.value,
                    'timestamp': alert.timestamp.isoformat(),
                    'source': alert.source,
                    'escalation_count': alert.escalation_count
                })
            
            # Add recent resolved alerts
            for alert_row in alert_history[:limit-len(active_alerts)]:
                all_alerts.append({
                    'id': alert_row['id'],
                    'title': alert_row['title'],
                    'level': alert_row['level'],
                    'status': alert_row['status'],
                    'timestamp': alert_row['timestamp'],
                    'source': alert_row['source'],
                    'escalation_count': alert_row['escalation_count']
                })
            
            # Sort by timestamp (most recent first)
            all_alerts.sort(key=lambda x: x['timestamp'], reverse=True)
            
            return all_alerts[:limit]
            
        except Exception as e:
            self.logger.error(f"Error getting recent alerts: {e}")
            return []
    
    def _get_health_recommendations(self) -> List[str]:
        """Get health recommendations"""
        try:
            current_health = self.health_calculator.calculate_health_score()
            return current_health.recommendations
        except Exception as e:
            self.logger.error(f"Error getting health recommendations: {e}")
            return [f"Error getting recommendations: {str(e)}"]
    
    def _calculate_uptime(self) -> str:
        """Calculate system uptime"""
        try:
            uptime_score = self.health_calculator.calculate_uptime_score(24)
            return f"{uptime_score:.2f}%"
        except Exception:
            return "Unknown"

class DashboardRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the metrics dashboard"""
    
    def __init__(self, dashboard: MetricsDashboard, *args, **kwargs):
        self.dashboard = dashboard
        super().__init__(*args, **kwargs)
    
    def do_GET(self):
        """Handle GET requests"""
        try:
            parsed_path = urlparse.urlparse(self.path)
            path = parsed_path.path
            query_params = urlparse.parse_qs(parsed_path.query)
            
            # Authentication check
            if self.dashboard.config.enable_auth:
                auth_token = query_params.get('token', [None])[0]
                if auth_token != self.dashboard.config.auth_token:
                    self._send_error(401, "Unauthorized")
                    return
            
            # Route requests
            if path == '/' or path == '/dashboard':
                self._serve_dashboard()
            elif path == '/api/current':
                self._serve_current_data()
            elif path == '/api/series':
                self._serve_metrics_series()
            elif path == '/api/historical':
                hours = int(query_params.get('hours', [24])[0])
                self._serve_historical_data(hours)
            elif path == '/api/alerts':
                self._serve_alerts()
            elif path == '/api/health':
                self._serve_health_data()
            elif path == '/health':
                self._serve_health_check()
            else:
                self._send_error(404, "Not Found")
                
        except Exception as e:
            self.dashboard.logger.error(f"Error handling request {self.path}: {e}")
            self._send_error(500, f"Internal Server Error: {str(e)}")
    
    def _serve_dashboard(self):
        """Serve the main dashboard HTML"""
        html = self._generate_dashboard_html()
        self._send_response(200, 'text/html', html)
    
    def _serve_current_data(self):
        """Serve current metrics data as JSON"""
        data = self.dashboard.get_current_data()
        self._send_json_response(data)
    
    def _serve_metrics_series(self):
        """Serve metrics time series data"""
        series = self.dashboard.get_metrics_series()
        self._send_json_response(series)
    
    def _serve_historical_data(self, hours: int):
        """Serve historical data"""
        data = self.dashboard.get_historical_data(hours)
        self._send_json_response(data)
    
    def _serve_alerts(self):
        """Serve alerts data"""
        alerts = self.dashboard._get_recent_alerts(20)
        self._send_json_response({'alerts': alerts})
    
    def _serve_health_data(self):
        """Serve health data"""
        try:
            health = self.dashboard.health_calculator.calculate_health_score()
            health_data = {
                'overall_score': health.overall_score,
                'status': health.status,
                'component_scores': health.component_scores,
                'recommendations': health.recommendations,
                'timestamp': health.timestamp.isoformat()
            }
            self._send_json_response(health_data)
        except Exception as e:
            self._send_json_response({'error': str(e)})
    
    def _serve_health_check(self):
        """Serve simple health check"""
        system_status = self.dashboard._get_system_status()
        self._send_json_response({
            'status': 'healthy' if system_status['status'] in ['excellent', 'good'] else 'degraded',
            'timestamp': datetime.now().isoformat()
        })
    
    def _send_response(self, status_code: int, content_type: str, content: str):
        """Send HTTP response"""
        self.send_response(status_code)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(content.encode('utf-8'))
    
    def _send_json_response(self, data: Any):
        """Send JSON response"""
        json_data = json.dumps(data, indent=2, default=str)
        self._send_response(200, 'application/json', json_data)
    
    def _send_error(self, status_code: int, message: str):
        """Send error response"""
        error_data = {'error': message, 'status_code': status_code}
        json_data = json.dumps(error_data, indent=2)
        self._send_response(status_code, 'application/json', json_data)
    
    def log_message(self, format, *args):
        """Override to use our logger"""
        self.dashboard.logger.debug(f"{self.client_address[0]} - {format % args}")
    
    def _generate_dashboard_html(self) -> str:
        """Generate the dashboard HTML page"""
        return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Rebuild - System Monitoring Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #f8f9fa;
            color: #333;
        }
        
        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 1rem 2rem;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        
        .header h1 {
            font-size: 1.5rem;
            font-weight: 600;
        }
        
        .container {
            max-width: 1400px;
            margin: 0 auto;
            padding: 2rem;
        }
        
        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2rem;
        }
        
        .metric-card {
            background: white;
            border-radius: 10px;
            padding: 1.5rem;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            border-left: 4px solid #667eea;
        }
        
        .metric-card h3 {
            font-size: 0.9rem;
            text-transform: uppercase;
            color: #666;
            margin-bottom: 0.5rem;
            font-weight: 600;
            letter-spacing: 0.5px;
        }
        
        .metric-value {
            font-size: 2rem;
            font-weight: 700;
            margin-bottom: 0.5rem;
        }
        
        .metric-subtitle {
            font-size: 0.85rem;
            color: #888;
        }
        
        .status-excellent { color: #28a745; }
        .status-good { color: #17a2b8; }
        .status-degraded { color: #ffc107; }
        .status-critical { color: #dc3545; }
        .status-emergency { color: #dc3545; font-weight: 900; }
        
        .progress-bar {
            width: 100%;
            height: 8px;
            background: #e9ecef;
            border-radius: 4px;
            overflow: hidden;
            margin-top: 0.5rem;
        }
        
        .progress-fill {
            height: 100%;
            transition: width 0.5s ease;
            background: linear-gradient(90deg, #28a745, #ffc107, #dc3545);
        }
        
        .alerts-section {
            background: white;
            border-radius: 10px;
            padding: 1.5rem;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            margin-bottom: 2rem;
        }
        
        .alerts-section h2 {
            margin-bottom: 1rem;
            color: #333;
        }
        
        .alert-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 0.75rem;
            margin-bottom: 0.5rem;
            border-radius: 6px;
            border-left: 4px solid #ddd;
        }
        
        .alert-critical { border-left-color: #dc3545; background: #fff5f5; }
        .alert-warning { border-left-color: #ffc107; background: #fffbf0; }
        .alert-emergency { border-left-color: #dc3545; background: #ffebee; }
        
        .recommendations {
            background: white;
            border-radius: 10px;
            padding: 1.5rem;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }
        
        .recommendations h2 {
            margin-bottom: 1rem;
            color: #333;
        }
        
        .recommendations ul {
            list-style: none;
        }
        
        .recommendations li {
            padding: 0.5rem 0;
            border-bottom: 1px solid #eee;
        }
        
        .recommendations li:before {
            content: "→";
            color: #667eea;
            margin-right: 0.5rem;
        }
        
        .refresh-indicator {
            position: fixed;
            top: 20px;
            right: 20px;
            background: #28a745;
            color: white;
            padding: 0.5rem 1rem;
            border-radius: 20px;
            font-size: 0.8rem;
            opacity: 0;
            transition: opacity 0.3s;
        }
        
        .refresh-indicator.show {
            opacity: 1;
        }
        
        @media (max-width: 768px) {
            .container { padding: 1rem; }
            .header { padding: 1rem; }
            .metrics-grid { grid-template-columns: 1fr; }
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>🤖 AI Rebuild - System Monitoring Dashboard</h1>
        <div id="last-updated">Loading...</div>
    </div>
    
    <div class="refresh-indicator" id="refresh-indicator">
        ✓ Updated
    </div>
    
    <div class="container">
        <!-- System Overview -->
        <div class="metrics-grid" id="metrics-grid">
            <!-- Metrics will be loaded here -->
        </div>
        
        <!-- Active Alerts -->
        <div class="alerts-section">
            <h2>🚨 Recent Alerts</h2>
            <div id="alerts-list">Loading...</div>
        </div>
        
        <!-- Health Recommendations -->
        <div class="recommendations">
            <h2>💡 Health Recommendations</h2>
            <ul id="recommendations-list">
                <li>Loading recommendations...</li>
            </ul>
        </div>
    </div>

    <script>
        let refreshInterval;
        
        function updateDashboard() {
            fetch('/api/current')
                .then(response => response.json())
                .then(data => {
                    updateMetrics(data);
                    updateAlerts(data.recent_alerts || []);
                    updateRecommendations(data.health_recommendations || []);
                    updateLastUpdated();
                    showRefreshIndicator();
                })
                .catch(error => {
                    console.error('Error fetching data:', error);
                });
        }
        
        function updateMetrics(data) {
            const metrics = data.metrics || {};
            const health = data.health || {};
            const alerts = data.alerts || {};
            const systemStatus = data.system_status || {};
            
            const metricsHtml = `
                <div class="metric-card">
                    <h3>System Health</h3>
                    <div class="metric-value status-${systemStatus.status || 'unknown'}">
                        ${(health.overall_score || 0).toFixed(1)}%
                    </div>
                    <div class="metric-subtitle">Status: ${(systemStatus.status || 'unknown').toUpperCase()}</div>
                    <div class="progress-bar">
                        <div class="progress-fill" style="width: ${health.overall_score || 0}%"></div>
                    </div>
                </div>
                
                <div class="metric-card">
                    <h3>CPU Usage</h3>
                    <div class="metric-value">${(metrics.cpu_percent || 0).toFixed(1)}%</div>
                    <div class="metric-subtitle">System CPU utilization</div>
                    <div class="progress-bar">
                        <div class="progress-fill" style="width: ${metrics.cpu_percent || 0}%"></div>
                    </div>
                </div>
                
                <div class="metric-card">
                    <h3>Memory Usage</h3>
                    <div class="metric-value">${(metrics.memory_percent || 0).toFixed(1)}%</div>
                    <div class="metric-subtitle">System memory utilization</div>
                    <div class="progress-bar">
                        <div class="progress-fill" style="width: ${metrics.memory_percent || 0}%"></div>
                    </div>
                </div>
                
                <div class="metric-card">
                    <h3>Disk Usage</h3>
                    <div class="metric-value">${(metrics.disk_percent || 0).toFixed(1)}%</div>
                    <div class="metric-subtitle">Primary disk utilization</div>
                    <div class="progress-bar">
                        <div class="progress-fill" style="width: ${metrics.disk_percent || 0}%"></div>
                    </div>
                </div>
                
                <div class="metric-card">
                    <h3>Active Alerts</h3>
                    <div class="metric-value status-${alerts.total > 0 ? 'critical' : 'good'}">
                        ${alerts.total || 0}
                    </div>
                    <div class="metric-subtitle">
                        Critical: ${alerts.critical || 0}, Warning: ${alerts.warning || 0}
                    </div>
                </div>
                
                <div class="metric-card">
                    <h3>Database Response</h3>
                    <div class="metric-value">${((metrics.database_query_time || 0) * 1000).toFixed(0)}ms</div>
                    <div class="metric-subtitle">Average query time</div>
                </div>
            `;
            
            document.getElementById('metrics-grid').innerHTML = metricsHtml;
        }
        
        function updateAlerts(alerts) {
            if (!alerts.length) {
                document.getElementById('alerts-list').innerHTML = '<p>No recent alerts</p>';
                return;
            }
            
            const alertsHtml = alerts.slice(0, 10).map(alert => `
                <div class="alert-item alert-${alert.level}">
                    <div>
                        <strong>${alert.title}</strong><br>
                        <small>${alert.source} - ${new Date(alert.timestamp).toLocaleString()}</small>
                    </div>
                    <div>
                        <span class="status-${alert.level}">${alert.level.toUpperCase()}</span>
                        ${alert.status === 'open' ? ' [OPEN]' : ''}
                    </div>
                </div>
            `).join('');
            
            document.getElementById('alerts-list').innerHTML = alertsHtml;
        }
        
        function updateRecommendations(recommendations) {
            if (!recommendations.length) {
                document.getElementById('recommendations-list').innerHTML = '<li>No recommendations at this time</li>';
                return;
            }
            
            const recommendationsHtml = recommendations.map(rec => `<li>${rec}</li>`).join('');
            document.getElementById('recommendations-list').innerHTML = recommendationsHtml;
        }
        
        function updateLastUpdated() {
            const now = new Date().toLocaleTimeString();
            document.getElementById('last-updated').textContent = `Last updated: ${now}`;
        }
        
        function showRefreshIndicator() {
            const indicator = document.getElementById('refresh-indicator');
            indicator.classList.add('show');
            setTimeout(() => indicator.classList.remove('show'), 1000);
        }
        
        // Initialize dashboard
        document.addEventListener('DOMContentLoaded', function() {
            updateDashboard();
            refreshInterval = setInterval(updateDashboard, 5000); // Refresh every 5 seconds
        });
        
        // Cleanup on page unload
        window.addEventListener('beforeunload', function() {
            if (refreshInterval) {
                clearInterval(refreshInterval);
            }
        });
    </script>
</body>
</html>
        """