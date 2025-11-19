"""
Health score calculation system for production monitoring.
Calculates overall system health with target of 97% availability.
"""

import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from datetime import datetime, timedelta
import sqlite3
import json
import math
from .resource_monitor import ResourceMetrics

@dataclass
class HealthComponent:
    """Individual health component configuration"""
    name: str
    weight: float
    ideal_value: Optional[float] = None
    max_acceptable: Optional[float] = None
    invert: bool = False  # For metrics where lower is better
    
@dataclass 
class HealthScore:
    """Health score result"""
    overall_score: float
    component_scores: Dict[str, float]
    status: str  # 'excellent', 'good', 'degraded', 'critical'
    timestamp: datetime
    recommendations: List[str]

class HealthScoreCalculator:
    """
    Calculates system health score based on multiple metrics.
    
    Target: 97% health score for production readiness
    
    Components:
    - System resources (CPU, memory, disk)
    - Database performance
    - Network connectivity
    - Process stability
    - Alert frequency
    """
    
    def __init__(self, db_path: str = "data/monitoring.db"):
        self.db_path = db_path
        self.logger = logging.getLogger(__name__)
        
        # Health component definitions with weights
        self.components = {
            'cpu_health': HealthComponent('CPU Usage', 0.20, ideal_value=10.0, max_acceptable=80.0),
            'memory_health': HealthComponent('Memory Usage', 0.20, ideal_value=30.0, max_acceptable=85.0),
            'disk_health': HealthComponent('Disk Usage', 0.15, ideal_value=50.0, max_acceptable=90.0),
            'database_health': HealthComponent('Database Performance', 0.20, ideal_value=0.1, max_acceptable=2.0, invert=True),
            'process_health': HealthComponent('Process Stability', 0.10, ideal_value=100.0, max_acceptable=50.0),
            'alert_health': HealthComponent('Alert Frequency', 0.10, ideal_value=0.0, max_acceptable=5.0, invert=True),
            'uptime_health': HealthComponent('System Uptime', 0.05, ideal_value=99.9, max_acceptable=95.0)
        }
        
        # Health thresholds
        self.thresholds = {
            'excellent': 97.0,
            'good': 90.0,
            'degraded': 75.0
        }
    
    def calculate_component_score(self, 
                                component: HealthComponent, 
                                current_value: float) -> float:
        """Calculate individual component health score (0-100)"""
        try:
            if component.ideal_value is None or component.max_acceptable is None:
                return 100.0
            
            # Handle inverted metrics (lower is better)
            if component.invert:
                if current_value <= component.ideal_value:
                    return 100.0
                elif current_value >= component.max_acceptable:
                    return 0.0
                else:
                    # Linear degradation from ideal to max acceptable
                    ratio = (current_value - component.ideal_value) / (component.max_acceptable - component.ideal_value)
                    return max(0.0, 100.0 * (1.0 - ratio))
            else:
                # Normal metrics (higher is worse for usage metrics)
                if current_value <= component.ideal_value:
                    return 100.0
                elif current_value >= component.max_acceptable:
                    return 0.0
                else:
                    # Linear degradation from ideal to max acceptable
                    ratio = (current_value - component.ideal_value) / (component.max_acceptable - component.ideal_value)
                    return max(0.0, 100.0 * (1.0 - ratio))
                    
        except Exception as e:
            self.logger.error(f"Error calculating component score for {component.name}: {e}")
            return 50.0  # Default to neutral score on error
    
    def get_recent_metrics(self, hours: int = 1) -> List[ResourceMetrics]:
        """Get recent metrics for health calculation"""
        try:
            start_time = datetime.now() - timedelta(hours=hours)
            
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                results = conn.execute('''
                    SELECT * FROM resource_metrics 
                    WHERE timestamp >= ?
                    ORDER BY timestamp DESC
                ''', (start_time,)).fetchall()
                
                metrics_list = []
                for row in results:
                    load_avg = json.loads(row['load_average']) if row['load_average'] else None
                    
                    metrics = ResourceMetrics(
                        timestamp=datetime.fromisoformat(row['timestamp']),
                        cpu_percent=row['cpu_percent'],
                        memory_percent=row['memory_percent'],
                        disk_percent=row['disk_percent'],
                        disk_io_read=row['disk_io_read'],
                        disk_io_write=row['disk_io_write'],
                        network_bytes_sent=row['network_bytes_sent'],
                        network_bytes_recv=row['network_bytes_recv'],
                        process_count=row['process_count'],
                        load_average=load_avg,
                        database_connections=row['database_connections'],
                        database_query_time=row['database_query_time']
                    )
                    metrics_list.append(metrics)
                
                return metrics_list
                
        except Exception as e:
            self.logger.error(f"Error getting recent metrics: {e}")
            return []
    
    def get_recent_alerts(self, hours: int = 24) -> List[Dict]:
        """Get recent alerts for health calculation"""
        try:
            start_time = datetime.now() - timedelta(hours=hours)
            
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                results = conn.execute('''
                    SELECT * FROM alerts 
                    WHERE timestamp >= ?
                    ORDER BY timestamp DESC
                ''', (start_time,)).fetchall()
                
                return [dict(row) for row in results]
                
        except Exception as e:
            self.logger.error(f"Error getting recent alerts: {e}")
            return []
    
    def calculate_average_metrics(self, metrics_list: List[ResourceMetrics]) -> Dict[str, float]:
        """Calculate average values from recent metrics"""
        if not metrics_list:
            return {}
        
        totals = {
            'cpu_percent': 0,
            'memory_percent': 0, 
            'disk_percent': 0,
            'database_query_time': 0,
            'process_count': 0
        }
        
        valid_db_times = 0
        
        for metrics in metrics_list:
            totals['cpu_percent'] += metrics.cpu_percent
            totals['memory_percent'] += metrics.memory_percent
            totals['disk_percent'] += metrics.disk_percent
            totals['process_count'] += metrics.process_count
            
            if metrics.database_query_time is not None:
                totals['database_query_time'] += metrics.database_query_time
                valid_db_times += 1
        
        count = len(metrics_list)
        averages = {}
        
        for key, total in totals.items():
            if key == 'database_query_time':
                averages[key] = total / valid_db_times if valid_db_times > 0 else 0.5
            else:
                averages[key] = total / count
        
        return averages
    
    def calculate_uptime_score(self, hours: int = 24) -> float:
        """Calculate uptime score based on service availability"""
        try:
            # For this implementation, we'll estimate uptime based on 
            # continuous metric collection
            start_time = datetime.now() - timedelta(hours=hours)
            expected_samples = (hours * 3600) // 30  # Assuming 30-second intervals
            
            with sqlite3.connect(self.db_path) as conn:
                actual_samples = conn.execute('''
                    SELECT COUNT(*) FROM resource_metrics 
                    WHERE timestamp >= ?
                ''', (start_time,)).fetchone()[0]
            
            if expected_samples == 0:
                return 100.0
            
            uptime_percentage = min(100.0, (actual_samples / expected_samples) * 100.0)
            return uptime_percentage
            
        except Exception as e:
            self.logger.error(f"Error calculating uptime score: {e}")
            return 95.0  # Default reasonable uptime
    
    def calculate_health_score(self, hours: int = 1) -> HealthScore:
        """Calculate overall system health score"""
        try:
            # Get recent data
            metrics_list = self.get_recent_metrics(hours)
            recent_alerts = self.get_recent_alerts(24)  # Look at alerts over 24h
            
            if not metrics_list:
                # No recent metrics - system may be down
                return HealthScore(
                    overall_score=0.0,
                    component_scores={},
                    status='critical',
                    timestamp=datetime.now(),
                    recommendations=['No recent metrics available - system may be offline']
                )
            
            # Calculate average metrics
            avg_metrics = self.calculate_average_metrics(metrics_list)
            
            # Calculate component scores
            component_scores = {}
            
            # CPU Health
            component_scores['cpu_health'] = self.calculate_component_score(
                self.components['cpu_health'], avg_metrics['cpu_percent']
            )
            
            # Memory Health
            component_scores['memory_health'] = self.calculate_component_score(
                self.components['memory_health'], avg_metrics['memory_percent']
            )
            
            # Disk Health
            component_scores['disk_health'] = self.calculate_component_score(
                self.components['disk_health'], avg_metrics['disk_percent']
            )
            
            # Database Health
            component_scores['database_health'] = self.calculate_component_score(
                self.components['database_health'], avg_metrics['database_query_time']
            )
            
            # Process Health (based on process count stability)
            expected_processes = 100  # Baseline expectation
            process_variance = abs(avg_metrics['process_count'] - expected_processes)
            component_scores['process_health'] = self.calculate_component_score(
                self.components['process_health'], process_variance
            )
            
            # Alert Health (based on recent alert frequency)
            alert_count = len([a for a in recent_alerts if a['level'] == 'critical'])
            component_scores['alert_health'] = self.calculate_component_score(
                self.components['alert_health'], alert_count
            )
            
            # Uptime Health
            uptime_score = self.calculate_uptime_score(24)
            component_scores['uptime_health'] = uptime_score
            
            # Calculate weighted overall score
            overall_score = 0.0
            total_weight = 0.0
            
            for component_name, component in self.components.items():
                if component_name in component_scores:
                    overall_score += component_scores[component_name] * component.weight
                    total_weight += component.weight
            
            if total_weight > 0:
                overall_score = overall_score / total_weight
            
            # Determine status
            if overall_score >= self.thresholds['excellent']:
                status = 'excellent'
            elif overall_score >= self.thresholds['good']:
                status = 'good'
            elif overall_score >= self.thresholds['degraded']:
                status = 'degraded'
            else:
                status = 'critical'
            
            # Generate recommendations
            recommendations = self._generate_recommendations(
                component_scores, avg_metrics, recent_alerts
            )
            
            return HealthScore(
                overall_score=overall_score,
                component_scores=component_scores,
                status=status,
                timestamp=datetime.now(),
                recommendations=recommendations
            )
            
        except Exception as e:
            self.logger.error(f"Error calculating health score: {e}")
            return HealthScore(
                overall_score=0.0,
                component_scores={},
                status='critical',
                timestamp=datetime.now(),
                recommendations=[f'Error calculating health score: {str(e)}']
            )
    
    def _generate_recommendations(self, 
                                component_scores: Dict[str, float],
                                avg_metrics: Dict[str, float],
                                recent_alerts: List[Dict]) -> List[str]:
        """Generate actionable recommendations based on health scores"""
        recommendations = []
        
        # CPU recommendations
        if component_scores.get('cpu_health', 100) < 70:
            if avg_metrics.get('cpu_percent', 0) > 80:
                recommendations.append('High CPU usage detected - consider scaling or optimization')
            
        # Memory recommendations
        if component_scores.get('memory_health', 100) < 70:
            if avg_metrics.get('memory_percent', 0) > 85:
                recommendations.append('High memory usage - check for memory leaks or scale memory')
        
        # Disk recommendations
        if component_scores.get('disk_health', 100) < 70:
            if avg_metrics.get('disk_percent', 0) > 90:
                recommendations.append('Disk space critical - clean up logs or expand storage')
        
        # Database recommendations
        if component_scores.get('database_health', 100) < 70:
            if avg_metrics.get('database_query_time', 0) > 1.0:
                recommendations.append('Database queries slow - consider indexing or query optimization')
        
        # Alert recommendations
        critical_alerts = [a for a in recent_alerts if a['level'] == 'critical']
        if len(critical_alerts) > 0:
            recommendations.append(f'{len(critical_alerts)} critical alerts in last 24h - review and address')
        
        # Uptime recommendations
        if component_scores.get('uptime_health', 100) < 95:
            recommendations.append('System uptime below target - investigate stability issues')
        
        # General recommendations based on overall score
        overall_score = sum(component_scores.values()) / len(component_scores) if component_scores else 0
        
        if overall_score < 75:
            recommendations.append('Overall system health degraded - immediate attention required')
        elif overall_score < 90:
            recommendations.append('System health suboptimal - schedule maintenance review')
        
        if not recommendations:
            recommendations.append('System health is good - maintain current monitoring')
        
        return recommendations
    
    def get_health_trend(self, days: int = 7) -> Dict[str, Any]:
        """Get health score trend over time"""
        try:
            trend_data = []
            
            # Calculate daily health scores
            for day_offset in range(days, 0, -1):
                day_start = datetime.now() - timedelta(days=day_offset)
                day_end = day_start + timedelta(days=1)
                
                # Get metrics for this day
                with sqlite3.connect(self.db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    results = conn.execute('''
                        SELECT * FROM resource_metrics 
                        WHERE timestamp >= ? AND timestamp < ?
                        ORDER BY timestamp
                    ''', (day_start, day_end)).fetchall()
                
                if results:
                    # Convert to ResourceMetrics objects
                    day_metrics = []
                    for row in results:
                        load_avg = json.loads(row['load_average']) if row['load_average'] else None
                        
                        metrics = ResourceMetrics(
                            timestamp=datetime.fromisoformat(row['timestamp']),
                            cpu_percent=row['cpu_percent'],
                            memory_percent=row['memory_percent'],
                            disk_percent=row['disk_percent'],
                            disk_io_read=row['disk_io_read'],
                            disk_io_write=row['disk_io_write'],
                            network_bytes_sent=row['network_bytes_sent'],
                            network_bytes_recv=row['network_bytes_recv'],
                            process_count=row['process_count'],
                            load_average=load_avg,
                            database_connections=row['database_connections'],
                            database_query_time=row['database_query_time']
                        )
                        day_metrics.append(metrics)
                    
                    # Calculate health score for this day
                    avg_metrics = self.calculate_average_metrics(day_metrics)
                    if avg_metrics:
                        # Simplified daily score calculation
                        cpu_score = self.calculate_component_score(
                            self.components['cpu_health'], avg_metrics['cpu_percent']
                        )
                        memory_score = self.calculate_component_score(
                            self.components['memory_health'], avg_metrics['memory_percent']
                        )
                        disk_score = self.calculate_component_score(
                            self.components['disk_health'], avg_metrics['disk_percent']
                        )
                        db_score = self.calculate_component_score(
                            self.components['database_health'], avg_metrics['database_query_time']
                        )
                        
                        daily_score = (cpu_score * 0.3 + memory_score * 0.3 + 
                                     disk_score * 0.2 + db_score * 0.2)
                        
                        trend_data.append({
                            'date': day_start.strftime('%Y-%m-%d'),
                            'score': daily_score,
                            'samples': len(day_metrics)
                        })
            
            # Calculate trend direction
            if len(trend_data) >= 2:
                recent_avg = sum(d['score'] for d in trend_data[-3:]) / len(trend_data[-3:])
                older_avg = sum(d['score'] for d in trend_data[:-3]) / max(1, len(trend_data[:-3]))
                trend_direction = 'improving' if recent_avg > older_avg else 'declining'
            else:
                trend_direction = 'stable'
            
            return {
                'trend_data': trend_data,
                'trend_direction': trend_direction,
                'average_score': sum(d['score'] for d in trend_data) / len(trend_data) if trend_data else 0,
                'min_score': min(d['score'] for d in trend_data) if trend_data else 0,
                'max_score': max(d['score'] for d in trend_data) if trend_data else 0
            }
            
        except Exception as e:
            self.logger.error(f"Error calculating health trend: {e}")
            return {
                'trend_data': [],
                'trend_direction': 'unknown',
                'average_score': 0,
                'error': str(e)
            }
    
    def is_healthy(self, target_score: float = 97.0) -> bool:
        """Check if system meets health target"""
        current_health = self.calculate_health_score()
        return current_health.overall_score >= target_score